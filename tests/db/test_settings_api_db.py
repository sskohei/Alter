"""#16: 交流設定・ブロック・通知設定 API の DB 統合テスト（docs/09・docs/15・docs/23）。

JWT の検証は #43 のテストで確認済みのため ``get_principal`` を差し替える。
Auth 管理 API は偽物、招待は共通ドメイン処理で実 DB に登録する。
"""

import json
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
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

CONSENT = "2026-11-interaction-v1"


@dataclass
class Api:
    app: FastAPI
    client: TestClient
    identities: dict[str, list[dict[str, str]]]

    def as_user(self, sub: uuid.UUID) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks")
        return self.client


def _make_api(engine: Engine, **overrides: Any) -> Iterator[Api]:
    identities: dict[str, list[dict[str, str]]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        uid = request.url.path.rsplit("/", 1)[1]
        if uid not in identities:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": uid, "identities": identities[uid]})

    values: dict[str, Any] = {
        "alter_mode": "live",
        "database_url": engine.url.render_as_string(hide_password=False),
        "supabase_secret_key": "test-only",
        "alter_consent_version": CONSENT,
        **overrides,
    }
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Api(app, client, identities)


@pytest.fixture
def api(engine: Engine) -> Iterator[Api]:
    yield from _make_api(engine)


@pytest.fixture
def users(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[list[uuid.UUID]]:
    """作った利用者。テスト後に会話・ジョブ・監査を先に消す（参加者 AI は RESTRICT のため）。"""
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
                "delete from public.background_jobs where id in ("
                "  select c.job_id from public.conversations c "
                "  join public.conversation_participants p on p.conversation_id = c.id "
                "  where p.agent_id = any(:a))"
            ),
            {"a": agent_ids},
        )
        conn.execute(
            text(
                "delete from public.conversations where id in ("
                "  select conversation_id from public.conversation_participants "
                "  where agent_id = any(:a))"
            ),
            {"a": agent_ids},
        )
        conn.execute(
            text("delete from public.audit_events where actor_profile_id = any(:ids)"),
            {"ids": created_users},
        )


def _new_user(
    engine: Engine, api: Api, users: list[uuid.UUID], *, invited: bool = True
) -> uuid.UUID:
    sub = uuid.uuid4()
    api.identities[str(sub)] = [{"provider": "google", "id": f"g-{sub.hex[:10]}"}]
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        if invited:
            user = AuthUser(sub, (AuthIdentity("google", f"g-{sub.hex[:10]}"),))
            inv.register_invitation(conn, user, operator="test")
    users.append(sub)
    return sub


def _new_agent(conn: Connection, owner: uuid.UUID, *, approved: bool = True) -> uuid.UUID:
    conn.execute(
        text(
            "insert into public.profiles (id, display_name) values (:id, 'u') "
            "on conflict (id) do nothing"
        ),
        {"id": owner},
    )
    agent_id = uuid.uuid4()
    conn.execute(
        text("insert into public.agents (id, owner_id, name) values (:id, :owner, 'A')"),
        {"id": agent_id, "owner": owner},
    )
    conn.execute(
        text(
            "insert into public.agent_profiles (agent_id, version, personality, values_text, "
            "speaking_style, interaction_policy, shareable_info, restrictions, approved_at) "
            "values (:id, 1, 'p', 'v', 's', 'i', 'sh', 'r', "
            + ("now()" if approved else "null")
            + ")"
        ),
        {"id": agent_id},
    )
    if approved:
        conn.execute(
            text(
                "update public.agents set status = 'active', approved_profile_version = 1 "
                "where id = :id"
            ),
            {"id": agent_id},
        )
    conn.execute(
        text("insert into public.agent_settings (agent_id) values (:id)"), {"id": agent_id}
    )
    return agent_id


@pytest.fixture
def owner_agent(engine: Engine, api: Api, users: list[uuid.UUID]) -> tuple[uuid.UUID, uuid.UUID]:
    sub = _new_user(engine, api, users)
    with engine.begin() as conn:
        return sub, _new_agent(conn, sub)


def _url(agent_id: uuid.UUID, suffix: str = "settings") -> str:
    return f"/api/v1/agents/{agent_id}/{suffix}"


def _error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def _queue_conversation(conn: Connection, *agents: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Scheduler が予約した状態（queued の会話とジョブ）を作る。"""
    job_id = conn.execute(
        text(
            "insert into public.background_jobs (kind, dedupe_key) "
            "values ('conversation.run', :key) returning id"
        ),
        {"key": f"test-{uuid.uuid4()}"},
    ).scalar_one()
    conv_id = conn.execute(
        text("insert into public.conversations (job_id) values (:job) returning id"),
        {"job": job_id},
    ).scalar_one()
    for ordinal, agent in enumerate(agents, start=1):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, false, true)"
            ),
            {"c": conv_id, "a": agent, "o": ordinal},
        )
    return conv_id, job_id


def _statuses(engine: Engine, conv_id: uuid.UUID, job_id: uuid.UUID) -> tuple[str, str]:
    with engine.connect() as conn:
        conv = conn.execute(
            text("select status from public.conversations where id = :id"), {"id": conv_id}
        ).scalar_one()
        job = conn.execute(
            text("select status from public.background_jobs where id = :id"), {"id": job_id}
        ).scalar_one()
    return conv, job


def _audits(engine: Engine, sub: uuid.UUID) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select action, resource_id, metadata from public.audit_events "
                "where actor_profile_id = :id order by created_at"
            ),
            {"id": sub},
        )
        return [dict(row._mapping) for row in rows]


# -- GET /agents/{id}/settings ------------------------------------------------


def test_initial_settings_are_off(api: Api, owner_agent: tuple[uuid.UUID, uuid.UUID]) -> None:
    sub, agent = owner_agent
    response = api.as_user(sub).get(_url(agent))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["autonomy_enabled"] is False
    assert body["discord_publish"] is False
    assert body["frequency_per_day"] == 1
    assert body["daily_budget_usd"] == 0.2
    assert body["max_session_usd"] == 0.05
    assert body["web_visibility"] == "participants"
    assert response.headers["cache-control"] == "no-store"


def test_other_users_agent_is_hidden(
    api: Api, engine: Engine, users: list[uuid.UUID], owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, agent = owner_agent
    stranger = _new_user(engine, api, users)
    client = api.as_user(stranger)
    _error(client.get(_url(agent)), 404, "NOT_FOUND")
    _error(client.patch(_url(agent), json={"frequency_per_day": 2}), 404, "NOT_FOUND")
    _error(client.put(_url(agent, f"blocks/{uuid.uuid4()}")), 404, "NOT_FOUND")
    _error(client.delete(_url(agent, f"blocks/{uuid.uuid4()}")), 404, "NOT_FOUND")
    _error(client.get(_url(uuid.uuid4())), 404, "NOT_FOUND")


def test_not_invited_user_is_rejected(api: Api, engine: Engine, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users, invited=False)
    _error(api.as_user(sub).get("/api/v1/preferences"), 403, "INVITATION_REQUIRED")


# -- PATCH /agents/{id}/settings ----------------------------------------------


def test_turn_on_records_consent_version(
    api: Api, engine: Engine, owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    sub, agent = owner_agent
    client = api.as_user(sub)
    response = client.patch(_url(agent), json={"autonomy_enabled": True, "frequency_per_day": 2})
    assert response.status_code == 200, response.text
    assert response.json()["autonomy_enabled"] is True
    assert response.json()["frequency_per_day"] == 2

    [audit] = _audits(engine, sub)
    assert audit["action"] == "agent_settings.update"
    assert audit["resource_id"] == str(agent)
    assert audit["metadata"] == {
        "changed": ["autonomy_enabled", "frequency_per_day"],
        "result": "success",
        "autonomy_enabled": True,
        "consent_version": CONSENT,
    }
    # 同じ値の再送は変更なし（監査も増えない）
    assert client.patch(_url(agent), json={"autonomy_enabled": True}).status_code == 200
    assert len(_audits(engine, sub)) == 1


def test_turn_on_requires_consent_terms(engine: Engine, users: list[uuid.UUID]) -> None:
    for api in _make_api(engine, alter_consent_version=""):
        sub = _new_user(engine, api, users)
        with engine.begin() as conn:
            agent = _new_agent(conn, sub)
        client = api.as_user(sub)
        _error(client.patch(_url(agent), json={"autonomy_enabled": True}), 409, "CONFLICT")
        # 同意を伴わない変更はできる
        assert client.patch(_url(agent), json={"frequency_per_day": 0}).status_code == 200


@pytest.mark.parametrize("field", ["autonomy_enabled", "discord_publish"])
def test_turn_on_requires_approved_active_agent(
    api: Api, engine: Engine, users: list[uuid.UUID], field: str
) -> None:
    sub = _new_user(engine, api, users)
    with engine.begin() as conn:
        agent = _new_agent(conn, sub, approved=False)
    _error(api.as_user(sub).patch(_url(agent), json={field: True}), 409, "CONFLICT")
    with engine.begin() as conn:
        conn.execute(
            text("update public.agent_profiles set approved_at = now() where agent_id = :id; "),
            {"id": agent},
        )
        conn.execute(
            text(
                "update public.agents set status = 'paused', approved_profile_version = 1 "
                "where id = :id"
            ),
            {"id": agent},
        )
    # 承認済みでも停止中（招待失効など）は ON にできない
    _error(api.as_user(sub).patch(_url(agent), json={field: True}), 409, "CONFLICT")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"autonomy_enabled": None},
        {"frequency_per_day": 3},
        {"frequency_per_day": -1},
        {"daily_budget_usd": -0.01},
        {"daily_budget_usd": 0.0000001},
        {"daily_budget_usd": 1.5},
        {"max_session_usd": 0.5},
        {"web_visibility": "public"},
        {"autonomy_enabled": "yes"},
        {"frequency_per_day": "2"},
        {"daily_budget_usd": "0.1"},
        {"unknown": True},
    ],
)
def test_invalid_settings_are_rejected(
    api: Api, engine: Engine, owner_agent: tuple[uuid.UUID, uuid.UUID], body: dict[str, Any]
) -> None:
    sub, agent = owner_agent
    _error(api.as_user(sub).patch(_url(agent), json=body), 422, "VALIDATION_ERROR")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select frequency_per_day, daily_budget_usd, max_session_usd "
                "from public.agent_settings where agent_id = :id"
            ),
            {"id": agent},
        ).one()
    assert (row.frequency_per_day, row.daily_budget_usd, row.max_session_usd) == (
        1,
        Decimal("0.200000"),
        Decimal("0.050000"),
    )
    assert _audits(engine, sub) == []


def test_budget_rules(api: Api, owner_agent: tuple[uuid.UUID, uuid.UUID]) -> None:
    sub, agent = owner_agent
    client = api.as_user(sub)
    # 日次予算を下げるときは、1 回あたりの上限も同時に下げる
    _error(client.patch(_url(agent), json={"daily_budget_usd": 0.01}), 422, "VALIDATION_ERROR")
    response = client.patch(_url(agent), json={"daily_budget_usd": 0.01, "max_session_usd": 0.01})
    assert response.status_code == 200, response.text
    assert response.json()["daily_budget_usd"] == 0.01
    # 運営の上限ちょうど・小数 6 桁は可
    response = client.patch(
        _url(agent), json={"daily_budget_usd": 1.0, "max_session_usd": 0.123456}
    )
    assert response.status_code == 200, response.text
    assert response.json()["max_session_usd"] == 0.123456
    # 0 は「使わない」として可
    assert (
        client.patch(_url(agent), json={"daily_budget_usd": 0, "max_session_usd": 0}).status_code
        == 200
    )


def test_turn_off_cancels_queued_interactions_only(
    api: Api, engine: Engine, users: list[uuid.UUID], owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    sub, agent = owner_agent
    other_sub = _new_user(engine, api, users)
    with engine.begin() as conn:
        other = _new_agent(conn, other_sub)
        third = _new_agent(conn, _new_user(engine, api, users))
        conn.execute(
            text("update public.agent_settings set autonomy_enabled = true where agent_id = :id"),
            {"id": agent},
        )
        mine = _queue_conversation(conn, agent, other)
        unrelated = _queue_conversation(conn, other, third)
        running_conv, running_job = _queue_conversation(conn, agent, third)
        conn.execute(
            text("update public.conversations set status = 'running' where id = :id"),
            {"id": running_conv},
        )

    response = api.as_user(sub).patch(_url(agent), json={"autonomy_enabled": False})
    assert response.status_code == 200, response.text
    assert _statuses(engine, *mine) == ("cancelled", "cancelled")
    assert _statuses(engine, *unrelated) == ("queued", "queued")
    # 実行中は Worker が発言前の再確認で止める（ここでは変更しない）
    assert _statuses(engine, running_conv, running_job)[0] == "running"
    [audit] = _audits(engine, sub)
    assert audit["metadata"]["cancelled_conversations"] == 1
    assert audit["metadata"]["autonomy_enabled"] is False
    assert "consent_version" not in audit["metadata"]


# -- ブロック ---------------------------------------------------------------


def test_block_is_idempotent_and_cancels_queued(
    api: Api, engine: Engine, users: list[uuid.UUID], owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    sub, agent = owner_agent
    with engine.begin() as conn:
        other = _new_agent(conn, _new_user(engine, api, users))
        third = _new_agent(conn, _new_user(engine, api, users))
        with_other = _queue_conversation(conn, agent, other)
        with_third = _queue_conversation(conn, agent, third)
    client = api.as_user(sub)
    url = _url(agent, f"blocks/{other}")
    for _ in range(2):
        response = client.put(url)
        assert response.status_code == 204, response.text
        assert response.content == b""
    with engine.connect() as conn:
        count = conn.execute(
            text("select count(*) from public.agent_blocks where blocker_agent_id = :a"),
            {"a": agent},
        ).scalar_one()
    assert count == 1
    assert _statuses(engine, *with_other) == ("cancelled", "cancelled")
    assert _statuses(engine, *with_third) == ("queued", "queued")
    [audit] = _audits(engine, sub)
    assert audit["action"] == "agent.block"
    # ブロックした相手の ID は監査に残さない
    assert str(other) not in json.dumps(audit["metadata"])

    for _ in range(2):
        assert client.delete(url).status_code == 204
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from public.agent_blocks where blocker_agent_id = :a"),
                {"a": agent},
            ).scalar_one()
            == 0
        )
    assert [a["action"] for a in _audits(engine, sub)] == ["agent.block", "agent.unblock"]


def test_block_rejects_self_and_unknown(api: Api, owner_agent: tuple[uuid.UUID, uuid.UUID]) -> None:
    sub, agent = owner_agent
    client = api.as_user(sub)
    _error(client.put(_url(agent, f"blocks/{agent}")), 422, "VALIDATION_ERROR")
    _error(client.delete(_url(agent, f"blocks/{agent}")), 422, "VALIDATION_ERROR")
    _error(client.put(_url(agent, f"blocks/{uuid.uuid4()}")), 404, "NOT_FOUND")
    # 解除はブロックしていない相手でも 204
    assert client.delete(_url(agent, f"blocks/{uuid.uuid4()}")).status_code == 204


# -- 通知設定 -----------------------------------------------------------------


def test_preferences_defaults_and_update(api: Api, engine: Engine, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    client = api.as_user(sub)
    response = client.get("/api/v1/preferences")
    assert response.status_code == 200, response.text
    assert response.json() == {"web_notifications": True, "discord_dm_notifications": False}

    response = client.patch("/api/v1/preferences", json={"discord_dm_notifications": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"web_notifications": True, "discord_dm_notifications": True}
    [audit] = _audits(engine, sub)
    assert audit["action"] == "preferences.update"
    assert audit["metadata"] == {
        "changed": ["discord_dm_notifications"],
        "result": "success",
        "discord_dm_notifications": True,
    }

    for body in ({}, {"web_notifications": None}, {"web_notifications": "no"}, {"x": True}):
        _error(client.patch("/api/v1/preferences", json=body), 422, "VALIDATION_ERROR")
    assert len(_audits(engine, sub)) == 1


# -- #69 レビュー指摘への回帰テスト -------------------------------------------------


def test_turn_off_is_allowed_after_operator_lowers_cap(
    engine: Engine, users: list[uuid.UUID]
) -> None:
    # 既定の日次予算 $0.20 のまま、運営が上限を $0.10 に下げた状態
    for api in _make_api(engine, agent_daily_budget_max_usd="0.10"):
        sub = _new_user(engine, api, users)
        with engine.begin() as conn:
            agent = _new_agent(conn, sub)
            conn.execute(
                text(
                    "update public.agent_settings set autonomy_enabled = true, "
                    "discord_publish = true where agent_id = :id"
                ),
                {"id": agent},
            )
        client = api.as_user(sub)
        # 同意の撤回と頻度の変更は、既存予算を直さなくても通る
        for body in (
            {"autonomy_enabled": False},
            {"discord_publish": False},
            {"frequency_per_day": 2},
        ):
            response = client.patch(_url(agent), json=body)
            assert response.status_code == 200, (body, response.text)
        # 再 ON と予算の変更は上限を確認する
        _error(client.patch(_url(agent), json={"autonomy_enabled": True}), 422, "VALIDATION_ERROR")
        _error(client.patch(_url(agent), json={"max_session_usd": 0.01}), 422, "VALIDATION_ERROR")
        ok = client.patch(
            _url(agent),
            json={"autonomy_enabled": True, "daily_budget_usd": 0.1, "max_session_usd": 0.05},
        )
        assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("value", [1e30, 1e308, 1000000.000001])
def test_huge_budget_is_validation_error_not_500(
    api: Api, owner_agent: tuple[uuid.UUID, uuid.UUID], value: float
) -> None:
    sub, agent = owner_agent
    _error(
        api.as_user(sub).patch(_url(agent), json={"daily_budget_usd": value}),
        422,
        "VALIDATION_ERROR",
    )


def test_mutual_blocks_in_parallel_do_not_deadlock(
    api: Api, engine: Engine, users: list[uuid.UUID]
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from alter_domain import interaction_settings as domain

    a, b = _new_user(engine, api, users), _new_user(engine, api, users)
    with engine.begin() as conn:
        agent_a, agent_b = _new_agent(conn, a), _new_agent(conn, b)
    rounds = 15
    barrier = Barrier(2)

    def run(owner: uuid.UUID, me: uuid.UUID, other: uuid.UUID) -> list[str]:
        results = []
        for _ in range(rounds):
            barrier.wait()
            try:
                with engine.begin() as conn:
                    domain.block_agent(conn, owner, me, other)
                with engine.begin() as conn:
                    domain.unblock_agent(conn, owner, me, other)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001 - デッドロック（40P01）を数える
                results.append(type(exc).__name__)
        return results

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, a, agent_a, agent_b), pool.submit(run, b, agent_b, agent_a)]
        outcomes = [r for f in futures for r in f.result()]
    assert outcomes == ["ok"] * (rounds * 2)


def test_queued_conversation_taken_by_worker_is_not_cancelled(
    api: Api, engine: Engine, users: list[uuid.UUID], owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    sub, agent = owner_agent
    with engine.begin() as conn:
        other = _new_agent(conn, _new_user(engine, api, users))
        conn.execute(
            text("update public.agent_settings set autonomy_enabled = true where agent_id = :id"),
            {"id": agent},
        )
        waiting = _queue_conversation(conn, agent, other)
        taken_conv, taken_job = _queue_conversation(conn, agent, other)
        # Worker がジョブを取得済み（会話はまだ queued）
        conn.execute(
            text(
                "update public.background_jobs set status = 'running', "
                "lease_until = now() + interval '2 minutes' where id = :id"
            ),
            {"id": taken_job},
        )
    response = api.as_user(sub).patch(_url(agent), json={"autonomy_enabled": False})
    assert response.status_code == 200, response.text
    assert _statuses(engine, *waiting) == ("cancelled", "cancelled")
    assert _statuses(engine, taken_conv, taken_job) == ("queued", "running")
    [audit] = _audits(engine, sub)
    assert audit["metadata"]["cancelled_conversations"] == 1


def test_generated_settings_client_against_live_api(
    api: Api, engine: Engine, users: list[uuid.UUID], owner_agent: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Generated TS client, real HTTP/SQL, cancellation and consent audit in one flow."""
    import shutil
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path

    import uvicorn
    from fastapi import Request

    sub, agent = owner_agent
    other_sub = _new_user(engine, api, users)
    third_sub = _new_user(engine, api, users)
    with engine.begin() as conn:
        other = _new_agent(conn, other_sub)
        third = _new_agent(conn, third_sub)
        blocked_pair = _queue_conversation(conn, agent, other)
        stopped_pair = _queue_conversation(conn, agent, third)
        unrelated_pair = _queue_conversation(conn, other, third)

    def principal(request: Request) -> Principal:
        token = request.headers["authorization"].removeprefix("Bearer ")
        return Principal(uuid.UUID(token), "jwks")

    api.app.dependency_overrides[get_principal] = principal
    pnpm = shutil.which("pnpm")
    assert pnpm, "pnpm is required for generated-client integration"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(api.app, log_level="error"))
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
                    "tests/interaction-live.ts",
                    f"http://127.0.0.1:{port}",
                    str(sub),
                    str(other_sub),
                    str(agent),
                    str(other),
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
    assert _statuses(engine, *blocked_pair) == ("cancelled", "cancelled")
    assert _statuses(engine, *stopped_pair) == ("cancelled", "cancelled")
    assert _statuses(engine, *unrelated_pair) == ("queued", "queued")
    audits = _audits(engine, sub)
    assert len([a for a in audits if a["action"] == "agent.block"]) == 1
    assert len([a for a in audits if a["action"] == "agent.unblock"]) == 1
    assert any(a["metadata"].get("consent_version") == CONSENT for a in audits)
    assert any(a["metadata"].get("cancelled_conversations") == 1 for a in audits)
