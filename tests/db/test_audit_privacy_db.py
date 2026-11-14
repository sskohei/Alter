"""#32: 重要な操作の監査と、監査・ログ・応答に本文と秘密が出ないことの横断確認（docs/15）。

本人の重要な操作（人格の承認・交流の ON/OFF・ブロック・通知の設定・記憶の削除・削除要求）を
目印の文字列を入れた入力で一通り行い、

- 操作ごとに本人を主体とする監査が 1 件ずつ残る（交流 ON は同意文面の版も残る）
- 監査の全列・アプリのログ・エラー応答に、人格や表示名などの本文・token が出ない

ことを確かめる。Discord の公開・連携の監査は Bot・連携のテスト（test_discord_*）で確認する。
"""

import base64
import logging
import os
import time
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

MARK = "MARK-7f3a"  # 本文に入れる目印。監査・ログ・応答に出てはいけない
TOKEN = "TOKEN-9c1e-should-not-be-logged"
CONSENT = "2026-11-test"


def _draft(tag: str) -> dict[str, Any]:
    return {
        "name": f"分身{tag}",
        "personality": f"{MARK}-personality",
        "values_text": f"{MARK}-values",
        "interests": [f"{MARK}-interest"],
        "speaking_style": "丁寧",
        "interaction_policy": "尊重",
        "shareable_info": f"{MARK}-shareable",
        "restrictions": "推測禁止",
        "private_info": f"{MARK}-private",
    }


@dataclass
class World:
    app: FastAPI
    client: TestClient
    owner: uuid.UUID
    agent: uuid.UUID
    other_agent: uuid.UUID
    memory: uuid.UUID

    def call(self, method: str, url: str, **kwargs: Any) -> Any:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": f"audit-{uuid.uuid4().hex}",
            **kwargs.pop("headers", {}),
        }
        return self.client.request(method, url, headers=headers, **kwargs)


@pytest.fixture
def world(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[World]:
    owner, other = uuid.uuid4(), uuid.uuid4()
    created_users.extend([owner, other])
    with engine.begin() as conn:
        for sub in (owner, other):
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
        alter_consent_version=CONSENT,
        profile_encryption_key_v1=base64.b64encode(os.urandom(32)).decode(),
        api_idempotency_hmac_keys={1: SecretStr(base64.b64encode(os.urandom(32)).decode())},
    )
    conversation = None
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app = create_app(settings, http_client=http)
            with TestClient(app, raise_server_exceptions=False) as client:
                agents = []
                for sub, tag in ((owner, "A"), (other, "B")):
                    app.dependency_overrides[get_principal] = lambda s=sub: Principal(
                        s, "jwks", (("oauth", int(time.time())),)
                    )
                    created = client.post(
                        "/api/v1/agents",
                        json=_draft(tag),
                        headers={"Idempotency-Key": f"audit-create-{sub.hex}"},
                    )
                    assert created.status_code == 201, created.text
                    agents.append(uuid.UUID(created.json()["id"]))
                with engine.begin() as conn:
                    conversation = conn.execute(
                        text(
                            "insert into public.conversations (status, started_at, ended_at) "
                            "values ('completed', now(), now()) returning id"
                        )
                    ).scalar_one()
                    for ordinal, agent in enumerate(agents, 1):
                        conn.execute(
                            text(
                                "insert into public.conversation_participants (conversation_id,"
                                " agent_id, ordinal, web_consent, discord_consent) "
                                "values (:c, :a, :n, true, false)"
                            ),
                            {"c": conversation, "a": agent, "n": ordinal},
                        )
                    memory = conn.execute(
                        text(
                            "insert into public.memories "
                            "(agent_id, source_conversation_id, text, importance, dedupe_key) "
                            "values (:a, :c, :t, 3, :k) returning id"
                        ),
                        {"a": agents[0], "c": conversation, "t": f"{MARK}-memory", "k": "audit"},
                    ).scalar_one()
                app.dependency_overrides[get_principal] = lambda: Principal(
                    owner, "jwks", (("oauth", int(time.time())),)
                )
                yield World(app, client, owner, agents[0], agents[1], memory)
    finally:
        with engine.begin() as conn:
            if conversation is not None:
                conn.execute(
                    text("delete from public.conversations where id = :c"), {"c": conversation}
                )
            # 削除要求の操作で作った削除ジョブを残さない（ほかのテストのパージに拾われるため）
            conn.execute(
                text(
                    "delete from public.background_jobs where kind = 'account.purge' and "
                    "payload ->> 'deletion_request_id' in (select id::text from "
                    "public.deletion_requests where profile_id = any(:s))"
                ),
                {"s": [owner, other]},
            )
            conn.execute(
                text("delete from public.deletion_requests where profile_id = any(:s)"),
                {"s": [owner, other]},
            )
            conn.execute(
                text(
                    "delete from public.audit_events where actor_profile_id = any(:s) "
                    "or resource_id = any(cast(:t as text[]))"
                ),
                {"s": [owner, other], "t": [str(owner), str(other)]},
            )


def _audit_rows(engine: Engine, owner: uuid.UUID) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "select action, resource_type, resource_id, metadata, "
                    "row_to_json(a)::text as whole from public.audit_events a "
                    "where actor_profile_id = :o order by created_at, id"
                ),
                {"o": owner},
            )
        )


def test_important_operations_are_audited_without_content(
    engine: Engine, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    a = f"/api/v1/agents/{world.agent}"
    steps = [
        ("PATCH", "/api/v1/users/me", {"display_name": f"{MARK}-display"}, 200),
        ("PATCH", a, {"name": f"分身A{MARK}"}, 200),
        ("POST", f"{a}/profile/approve", {"version": 1}, 200),
        ("PATCH", f"{a}/settings", {"autonomy_enabled": True, "frequency_per_day": 1}, 200),
        ("PATCH", f"{a}/settings", {"autonomy_enabled": False}, 200),
        ("PUT", f"{a}/blocks/{world.other_agent}", None, 204),
        ("DELETE", f"{a}/blocks/{world.other_agent}", None, 204),
        ("PATCH", "/api/v1/preferences", {"discord_dm_notifications": True}, 200),
        ("DELETE", f"/api/v1/memories/{world.memory}", None, 204),
        ("POST", "/api/v1/users/me/deletion", {"confirm": True}, 202),
    ]
    for method, url, body, expected in steps:
        response = world.call(method, url, json=body)
        assert response.status_code == expected, (method, url, response.text)

    rows = _audit_rows(engine, world.owner)
    actions = [row.action for row in rows]
    for action in (
        "profile.approve",
        "agent_settings.update",
        "agent.block",
        "agent.unblock",
        "preferences.update",
        "memory.delete",
        "account.deletion_request",
    ):
        assert action in actions, action
    assert actions.count("agent_settings.update") == 2
    turned_on = next(
        row.metadata for row in rows
        if row.action == "agent_settings.update" and row.metadata.get("autonomy_enabled")
    )  # fmt: skip
    # 交流の ON は同意文面の版と一緒に残る（docs/15）
    assert turned_on["consent_version"] == CONSENT

    # 監査の全列・アプリのログに本文や token が出ない
    for row in rows:
        assert MARK not in row.whole, row.action
        assert TOKEN not in row.whole
    assert MARK not in caplog.text
    assert TOKEN not in caplog.text


def test_error_responses_do_not_reflect_input(world: World) -> None:
    a = f"/api/v1/agents/{world.agent}"
    cases = [
        ("PATCH", "/api/v1/users/me", {"display_name": f"{MARK}" * 100}),  # 長すぎる
        ("PATCH", a, {"name": MARK, "unknown": MARK}),  # 未知の項目
        ("POST", f"{a}/profile/approve", {"version": MARK}),  # 型の誤り
        ("PATCH", f"{a}/settings", {"daily_budget_usd": MARK}),
        ("POST", "/api/v1/users/me/deletion", {"confirm": MARK}),
    ]
    for method, url, body in cases:
        response = world.call(method, url, json=body)
        assert response.status_code == 422, (method, url, response.text)
        assert MARK not in response.text
        assert TOKEN not in response.text
