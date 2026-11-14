"""#32: 他人の ID を使った操作の横断回帰テスト（docs/06・07・15）。

ID を受け取るすべての API について、所有者でない利用者（他人・会話の相手）が
読む・書く・消すことができず、所有者のデータが変わらないことを 1 か所で確かめる。

``MATRIX`` に載っていない「ID を受け取る API」が増えたら ``test_matrix_covers_every_id_route``
が失敗する。新しい API を足したら、ここに他人からの要求を足すこと。
"""

import base64
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.invitations import register_invitation
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text


def _draft(tag: str) -> dict[str, Any]:
    return {
        "name": f"分身-{tag}",
        "personality": f"穏やか-{tag}",
        "values_text": "対話",
        "interests": ["読書"],
        "speaking_style": "丁寧",
        "interaction_policy": "尊重",
        "shareable_info": f"公開できる情報-{tag}",
        "restrictions": "推測禁止",
        "private_info": f"非公開の秘密-{tag}",
    }


@dataclass
class World:
    app: FastAPI
    client: TestClient
    a: uuid.UUID
    b: uuid.UUID
    c: uuid.UUID
    agent_a: uuid.UUID
    agent_b: uuid.UUID
    agent_c: uuid.UUID
    conversation: uuid.UUID
    notification: uuid.UUID
    memory: uuid.UUID

    def as_user(self, sub: uuid.UUID) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks")
        return self.client


@pytest.fixture
def world(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[World]:
    subs = [uuid.uuid4() for _ in range(3)]
    created_users.extend(subs)
    with engine.begin() as conn:
        for sub in subs:
            conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
            register_invitation(
                conn, AuthUser(sub, (AuthIdentity("google", str(sub)),)), operator="sskohei"
            )

    def handler(request: httpx.Request) -> httpx.Response:
        uid = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"id": uid, "identities": [{"provider": "google", "id": uid}]}
        )

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        alter_mode="live",
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test-only",
        profile_encryption_key_v1=base64.b64encode(os.urandom(32)).decode(),
        api_idempotency_hmac_keys={1: SecretStr(base64.b64encode(os.urandom(32)).decode())},
    )
    conversation: uuid.UUID | None = None
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app = create_app(settings, http_client=http)
            with TestClient(app, raise_server_exceptions=False) as client:
                agents = []
                for sub, tag in zip(subs, "abc", strict=True):
                    app.dependency_overrides[get_principal] = lambda s=sub: Principal(s, "jwks")
                    created = client.post(
                        "/api/v1/agents",
                        json=_draft(tag),
                        headers={"Idempotency-Key": f"cross-{tag}-{sub.hex}"},
                    )
                    assert created.status_code == 201, created.text
                    agent = uuid.UUID(created.json()["id"])
                    approved = client.post(
                        f"/api/v1/agents/{agent}/profile/approve", json={"version": 1}
                    )
                    assert approved.status_code == 200, approved.text
                    agents.append(agent)
                with engine.begin() as conn:
                    conversation = conn.execute(
                        text(
                            "insert into public.conversations (status, started_at, ended_at) "
                            "values ('completed', now(), now()) returning id"
                        )
                    ).scalar_one()
                    for ordinal, agent in enumerate(agents[:2], 1):
                        conn.execute(
                            text(
                                "insert into public.conversation_participants "
                                "(conversation_id, agent_id, ordinal, web_consent, "
                                "discord_consent) values (:c, :a, :n, true, false)"
                            ),
                            {"c": conversation, "a": agent, "n": ordinal},
                        )
                    notification = conn.execute(
                        text(
                            "insert into public.notifications "
                            "(profile_id, kind, conversation_id, dedupe_key) "
                            "values (:p, 'conversation.completed', :c, :k) returning id"
                        ),
                        {"p": subs[0], "c": conversation, "k": f"cross-{conversation}"},
                    ).scalar_one()
                    memory = conn.execute(
                        text(
                            "insert into public.memories "
                            "(agent_id, source_conversation_id, text, importance, dedupe_key) "
                            "values (:a, :c, '所有者だけの記憶', 3, :k) returning id"
                        ),
                        {"a": agents[0], "c": conversation, "k": f"cross-{conversation}"},
                    ).scalar_one()
                yield World(app, client, *subs, *agents, conversation, notification, memory)
    finally:
        with engine.begin() as conn:
            if conversation is not None:
                conn.execute(
                    text("delete from public.conversations where id = :c"), {"c": conversation}
                )
            conn.execute(
                text(
                    "delete from public.usage_records where agent_id in "
                    "(select id from public.agents where owner_id = any(:s))"
                ),
                {"s": subs},
            )


# (method, path, body)。``{a}`` は A の AI、``{c_agent}`` は要求者 C の AI に置き換える
MATRIX: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/agents/{a}/profile", None),
    ("GET", "/api/v1/agents/{a}/profile/private", None),
    ("PATCH", "/api/v1/agents/{a}", {"name": "乗っ取り"}),
    ("POST", "/api/v1/agents/{a}/profile/revisions", {**_draft("x"), "expected_version": 1}),
    ("POST", "/api/v1/agents/{a}/profile/approve", {"version": 1}),
    ("POST", "/api/v1/agents/{a}/profile/suggest", {"answers": {"personality": "強引"}}),
    ("GET", "/api/v1/agents/{a}/settings", None),
    ("PATCH", "/api/v1/agents/{a}/settings", {"autonomy_enabled": True}),
    ("PUT", "/api/v1/agents/{a}/blocks/{c_agent}", None),
    ("DELETE", "/api/v1/agents/{a}/blocks/{c_agent}", None),
    ("GET", "/api/v1/conversations/{conversation}", None),
    ("PATCH", "/api/v1/notifications/{notification}/read", None),
    ("DELETE", "/api/v1/memories/{memory}", None),
    ("GET", "/api/v1/memories?agent_id={a}", None),
]
# 会話の相手（B）にも見せてはいけないもの（会話そのものは相手も見られる）
PARTNER_DENIED = {
    "/api/v1/notifications/{notification}/read",
    "/api/v1/memories/{memory}",
    "/api/v1/memories?agent_id={a}",
    "/api/v1/agents/{a}/profile/private",
    "/api/v1/agents/{a}/settings",
}


def _url(template: str, world: World, actor_agent: uuid.UUID) -> str:
    return template.format(
        a=world.agent_a,
        c_agent=actor_agent,
        conversation=world.conversation,
        notification=world.notification,
        memory=world.memory,
    )


def _snapshot(engine: Engine, world: World) -> dict[str, Any]:
    with engine.connect() as conn:
        return {
            "agent": tuple(
                conn.execute(
                    text(
                        "select name, status, approved_profile_version, icon_path "
                        "from public.agents where id = :a"
                    ),
                    {"a": world.agent_a},
                ).one()
            ),
            "profiles": conn.execute(
                text("select count(*) from public.agent_profiles where agent_id = :a"),
                {"a": world.agent_a},
            ).scalar_one(),
            "settings": tuple(
                conn.execute(
                    text(
                        "select autonomy_enabled, frequency_per_day, daily_budget_usd "
                        "from public.agent_settings where agent_id = :a"
                    ),
                    {"a": world.agent_a},
                ).one()
            ),
            "blocks": conn.execute(
                text(
                    "select count(*) from public.agent_blocks "
                    "where blocker_agent_id = :a or blocked_agent_id = :a"
                ),
                {"a": world.agent_a},
            ).scalar_one(),
            "notification": conn.execute(
                text("select read_at from public.notifications where id = :n"),
                {"n": world.notification},
            ).scalar_one(),
            "memory": conn.execute(
                text("select status from public.memories where id = :m"), {"m": world.memory}
            ).scalar_one(),
            "llm_runs": conn.execute(
                text("select count(*) from public.llm_runs where agent_id = :a"),
                {"a": world.agent_a},
            ).scalar_one(),
        }


def _request(client: TestClient, method: str, url: str, body: dict[str, Any] | None) -> Any:
    headers = {"Idempotency-Key": f"cross-attack-{uuid.uuid4().hex}"}
    return client.request(method, url, json=body, headers=headers)


def _secrets(world: World) -> list[str]:
    # A のデータが応答に紛れ込んでいないか（名前・人格・非公開情報・記憶・ID）
    return ["分身-a", "穏やか-a", "非公開の秘密-a", "所有者だけの記憶", str(world.a)]


def test_owner_controls_succeed(world: World) -> None:
    # 対照: 同じ要求を所有者が行うと通る（他人の 404 が、URL や本文の誤りではないこと）
    owner = world.as_user(world.a)
    for path in (
        "/api/v1/agents/{a}/profile",
        "/api/v1/agents/{a}/profile/private",
        "/api/v1/agents/{a}/settings",
        "/api/v1/conversations/{conversation}",
        "/api/v1/memories?agent_id={a}",
    ):
        response = owner.get(_url(path, world, world.agent_a))
        assert response.status_code == 200, (path, response.text)
    # 会話の相手は会話そのものを見られる
    partner = world.as_user(world.b).get(
        _url("/api/v1/conversations/{conversation}", world, world.agent_b)
    )
    assert partner.status_code == 200


def test_stranger_cannot_touch_others_resources(engine: Engine, world: World) -> None:
    before = _snapshot(engine, world)
    stranger = world.as_user(world.c)
    for method, template, body in MATRIX:
        response = _request(stranger, method, _url(template, world, world.agent_c), body)
        # 存在を明かさないため 404（403 は使わない）
        assert response.status_code == 404, (method, template, response.status_code, response.text)
        assert response.json()["error"]["code"] == "NOT_FOUND"
        for secret in _secrets(world):
            assert secret not in response.text, (method, template)
    assert _snapshot(engine, world) == before


def test_partner_cannot_touch_owner_only_resources(engine: Engine, world: World) -> None:
    before = _snapshot(engine, world)
    partner = world.as_user(world.b)
    for method, template, body in MATRIX:
        if template not in PARTNER_DENIED:
            continue
        response = _request(partner, method, _url(template, world, world.agent_b), body)
        assert response.status_code == 404, (method, template, response.status_code, response.text)
        for secret in ["非公開の秘密-a", "所有者だけの記憶"]:
            assert secret not in response.text, (method, template)
    assert _snapshot(engine, world) == before


def test_lists_do_not_include_others_items(world: World) -> None:
    stranger = world.as_user(world.c)
    for path in ("/api/v1/conversations", "/api/v1/notifications", "/api/v1/relationships"):
        response = stranger.get(path)
        assert response.status_code == 200, (path, response.text)
        body = response.text
        assert str(world.conversation) not in body
        assert str(world.notification) not in body
        assert "分身-a" not in body


def test_matrix_covers_every_id_route(world: World) -> None:
    """ID をパスで受け取る API と、ID を query で受け取る一覧がすべて MATRIX にある。"""
    covered = {(m, t.split("?")[0]) for m, t, _ in MATRIX}
    placeholders = {
        "{a}": "{id}",
        "{c_agent}": "{other_id}",
        "{conversation}": "{id}",
        "{notification}": "{id}",
        "{memory}": "{id}",
    }

    def normalize(path: str) -> str:
        for name in ("{agent_id}", "{other_id}", "{id}"):
            path = path.replace(name, "{}")
        return path

    def from_matrix(path: str) -> str:
        for k, v in placeholders.items():
            path = path.replace(k, v)
        return normalize(path)

    covered_norm = {(m, from_matrix(p)) for m, p in covered}
    schema = world.app.openapi()
    missing = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            takes_id = "{" in path or any(
                p.get("in") == "query" and p["name"].endswith("_id")
                for p in operation.get("parameters", [])
            )
            if takes_id and (method.upper(), normalize(path)) not in covered_norm:
                missing.append(f"{method.upper()} {path}")
    assert missing == [], "MATRIX に他人からの要求を追加してください: " + ", ".join(missing)
