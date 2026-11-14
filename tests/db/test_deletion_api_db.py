"""#30 前半: アカウント削除要求の受付・即時停止・状況確認の DB 統合テスト（docs/06・docs/23）。

JWT の検証は差し替え、``amr``（再認証）は Principal に直接入れる。Auth 管理 API は偽物。
"""

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain import account_deletion
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

PATH = "/api/v1/users/me/deletion"


def _fresh() -> tuple[tuple[str, int], ...]:
    return (("oauth", int(time.time()) - 10),)


@dataclass
class Api:
    app: FastAPI
    client: TestClient
    identities: dict[str, list[dict[str, str]]]

    def as_user(self, sub: uuid.UUID, methods: tuple[tuple[str, int], ...] = ()) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks", methods)
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
    yield created_users
    with engine.begin() as conn:
        conn.execute(
            text(
                "delete from public.conversations where id in ("
                "  select p.conversation_id from public.conversation_participants p "
                "  join public.agents a on a.id = p.agent_id where a.owner_id = any(:ids))"
            ),
            {"ids": created_users},
        )
        request_ids = [
            str(r)
            for r in conn.execute(
                text("select id from public.deletion_requests where profile_id = any(:ids)"),
                {"ids": created_users},
            ).scalars()
        ]
        conn.execute(
            text("delete from public.background_jobs where kind = :k and dedupe_key = any(:keys)"),
            {
                "k": account_deletion.PURGE_JOB_KIND,
                "keys": [f"account.purge:{r}" for r in request_ids],
            },
        )
        conn.execute(
            text("delete from public.deletion_requests where profile_id = any(:ids)"),
            {"ids": created_users},
        )


def _user(engine: Engine, api: Api, users: list[uuid.UUID], *, agent: bool = True) -> uuid.UUID:
    sub = uuid.uuid4()
    pid = f"g-{sub.hex[:10]}"
    api.identities[str(sub)] = [{"provider": "google", "id": pid}]
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(conn, AuthUser(sub, (AuthIdentity("google", pid),)), operator="t")
        conn.execute(
            text("insert into public.profiles (id, display_name) values (:id, '本名')"),
            {"id": sub},
        )
        if agent:
            agent_id = uuid.uuid4()
            conn.execute(
                text("insert into public.agents (id, owner_id, name) values (:id, :o, 'AI')"),
                {"id": agent_id, "o": sub},
            )
            conn.execute(
                text(
                    "insert into public.agent_profiles (agent_id, version, personality, "
                    "values_text, speaking_style, interaction_policy, shareable_info, "
                    "restrictions, approved_at) "
                    "values (:id, 1, 'p', 'v', 's', 'i', 'sh', 'r', now())"
                ),
                {"id": agent_id},
            )
            conn.execute(
                text(
                    "update public.agents set status = 'active', approved_profile_version = 1 "
                    "where id = :id"
                ),
                {"id": agent_id},
            )
            conn.execute(
                text(
                    "insert into public.agent_settings (agent_id, autonomy_enabled) "
                    "values (:a, true)"
                ),
                {"a": agent_id},
            )
    users.append(sub)
    return sub


def _agent(conn: Connection, owner: uuid.UUID) -> uuid.UUID:
    return uuid.UUID(
        str(
            conn.execute(
                text("select id from public.agents where owner_id = :o"), {"o": owner}
            ).scalar_one()
        )
    )


def _queued_conversation(
    conn: Connection, a: uuid.UUID, b: uuid.UUID, *, job_status: str = "queued"
) -> uuid.UUID:
    job = conn.execute(
        text(
            "insert into public.background_jobs (kind, dedupe_key, status, lease_until) "
            "values ('conversation.run', :k, cast(:s as varchar), "
            "case when cast(:s as varchar) = 'running' then now() + interval '1 minute' end) "
            "returning id"
        ),
        {"k": f"t-{uuid.uuid4()}", "s": job_status},
    ).scalar_one()
    conv = conn.execute(
        text(
            "insert into public.conversations (status, job_id) values ('queued', :j) returning id"
        ),
        {"j": job},
    ).scalar_one()
    for ordinal, agent in ((1, a), (2, b)):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, false, true)"
            ),
            {"c": conv, "a": agent, "o": ordinal},
        )
    return uuid.UUID(str(conv))


def _state(conn: Connection, sub: uuid.UUID) -> dict[str, object]:
    row = conn.execute(
        text(
            "select p.deleted_at is not null as deleted, a.status as agent_status, "
            "  s.autonomy_enabled, i.status as invitation_status, "
            "  (select count(*) from public.deletion_requests d where d.profile_id = p.id) "
            "    as requests "
            "from public.profiles p "
            "left join public.agents a on a.owner_id = p.id "
            "left join public.agent_settings s on s.agent_id = a.id "
            "join public.invitations i on i.auth_user_id = p.id "
            "where p.id = :id"
        ),
        {"id": sub},
    ).one()
    return dict(row._mapping)


ACTIVE = {
    "deleted": False,
    "agent_status": "active",
    "autonomy_enabled": True,
    "invitation_status": "active",
    "requests": 0,
}


# -- 再認証 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "methods",
    [
        (),
        (("oauth", 0),),  # 古いログイン（refresh しても amr の時刻は変わらない）
        (("password", 10**10),),
        (("otp", int(time.time())),),
    ],
)
def test_requires_recent_oauth_reauthentication(
    engine: Engine, api: Api, users: list[uuid.UUID], methods: tuple[tuple[str, int], ...]
) -> None:
    sub = _user(engine, api, users)
    response = api.as_user(sub, methods).post(PATH, json={"confirm": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "REAUTHENTICATION_REQUIRED"
    with engine.connect() as conn:
        assert _state(conn, sub) == ACTIVE


def test_stale_oauth_just_outside_five_minutes_is_rejected(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _user(engine, api, users)
    stale = (("oauth", int(time.time()) - 301),)
    assert api.as_user(sub, stale).post(PATH, json={"confirm": True}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": True, "extra": 1}])
def test_confirm_true_is_required(
    engine: Engine, api: Api, users: list[uuid.UUID], body: dict[str, object]
) -> None:
    sub = _user(engine, api, users)
    assert api.as_user(sub, _fresh()).post(PATH, json=body).status_code == 422
    with engine.connect() as conn:
        assert _state(conn, sub) == ACTIVE


# -- 受付と即時停止 -----------------------------------------------------------------------


def test_request_stops_everything_in_one_transaction(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _user(engine, api, users)
    partner = _user(engine, api, users)
    with engine.begin() as conn:
        mine, theirs = _agent(conn, sub), _agent(conn, partner)
        queued = _queued_conversation(conn, mine, theirs)
        taken = _queued_conversation(conn, theirs, mine, job_status="running")
        done = conn.execute(
            text("insert into public.conversations (status) values ('completed') returning id")
        ).scalar_one()
        for ordinal, agent in ((1, mine), (2, theirs)):
            conn.execute(
                text(
                    "insert into public.conversation_participants "
                    "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                    "values (:c, :a, :o, false, true)"
                ),
                {"c": done, "a": agent, "o": ordinal},
            )
    # 相手からは共有会話が見えている
    assert api.as_user(partner).get("/api/v1/conversations").json()["items"]

    response = api.as_user(sub, _fresh()).post(
        PATH, json={"confirm": True}, headers={"Idempotency-Key": "k-1"}
    )
    assert response.status_code == 202, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["status"] == "pending" and body["completed_at"] is None
    assert set(body) == {"id", "status", "requested_at", "completed_at"}

    with engine.connect() as conn:
        assert _state(conn, sub) == {
            "deleted": True,
            "agent_status": "paused",
            "autonomy_enabled": False,
            "invitation_status": "revoked",
            "requests": 1,
        }
        statuses = dict(
            conn.execute(
                text(
                    "select c.id, c.status || '/' || j.status from public.conversations c "
                    "join public.background_jobs j on j.id = c.job_id where c.id in (:q, :t)"
                ),
                {"q": queued, "t": taken},
            ).all()
        )
        job = conn.execute(
            text(
                "select status, payload from public.background_jobs "
                "where kind = 'account.purge' and dedupe_key = :k"
            ),
            {"k": f"account.purge:{body['id']}"},
        ).one()
        audit = conn.execute(
            text(
                "select resource_id, metadata from public.audit_events "
                "where actor_profile_id = :p and action = 'account.deletion_request'"
            ),
            {"p": sub},
        ).one()
    # 未開始の交流だけ取り消す（Worker が取得済みのものは Worker が止める）
    assert statuses == {queued: "cancelled/cancelled", taken: "queued/running"}
    assert job.status == "queued" and job.payload == {"deletion_request_id": body["id"]}
    assert audit.resource_id == body["id"]
    assert audit.metadata == {"paused_agents": 1, "cancelled_interactions": 1}
    assert "本名" not in str(audit)

    # 以後の業務 API は 403、状況確認だけはできる
    me = api.as_user(sub).get("/api/v1/users/me")
    assert me.status_code == 403 and me.json()["error"]["code"] == "ACCOUNT_INACTIVE"
    status = api.as_user(sub).get(PATH)
    assert status.status_code == 200 and status.json() == body
    # 相手の画面からも共有会話が消える
    assert api.as_user(partner).get("/api/v1/conversations").json()["items"] == []


def test_resend_returns_same_request_without_reauthentication(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _user(engine, api, users, agent=False)
    first = api.as_user(sub, _fresh()).post(PATH, json={"confirm": True})
    assert first.status_code == 202
    again = api.as_user(sub).post(PATH, json={"confirm": True})
    assert again.status_code == 202 and again.json() == first.json()
    with engine.connect() as conn:
        assert _state(conn, sub)["requests"] == 1


def test_concurrent_requests_create_one(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _user(engine, api, users)
    api.as_user(sub, _fresh())
    barrier = threading.Barrier(4)
    codes: list[int] = []
    ids: list[str] = []

    def call() -> None:
        barrier.wait()
        response = api.client.post(PATH, json={"confirm": True})
        codes.append(response.status_code)
        ids.append(response.json().get("id", ""))

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [202] * 4 and len(set(ids)) == 1
    with engine.connect() as conn:
        assert _state(conn, sub)["requests"] == 1


def test_failure_rolls_back_everything(
    engine: Engine, api: Api, users: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = _user(engine, api, users)
    # ジョブの種類を不正にして、最後の手前で DB エラーを起こす
    monkeypatch.setattr(account_deletion, "PURGE_JOB_KIND", "Invalid Kind")
    response = api.as_user(sub, _fresh()).post(PATH, json={"confirm": True})
    assert response.status_code == 503
    with engine.connect() as conn:
        assert _state(conn, sub) == ACTIVE
    assert api.as_user(sub).get(PATH).status_code == 404


# -- 招待・状況確認 -------------------------------------------------------------------------


def test_uninvited_and_revoked_users_cannot_request(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    stranger = uuid.uuid4()
    api.identities[str(stranger)] = [{"provider": "google", "id": "g-stranger"}]
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": stranger})
    users.append(stranger)
    response = api.as_user(stranger, _fresh()).post(PATH, json={"confirm": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVITATION_REQUIRED"

    sub = _user(engine, api, users)
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.invitations set status = 'revoked', revoked_at = now() "
                "where auth_user_id = :id"
            ),
            {"id": sub},
        )
    response = api.as_user(sub, _fresh()).post(PATH, json={"confirm": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"


def test_status_is_404_before_request_and_only_own(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    a = _user(engine, api, users, agent=False)
    b = _user(engine, api, users, agent=False)
    response = api.as_user(a).get(PATH)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    api.as_user(b, _fresh()).post(PATH, json={"confirm": True})
    assert api.as_user(a).get(PATH).status_code == 404
    assert api.as_user(b).get(PATH).status_code == 200
