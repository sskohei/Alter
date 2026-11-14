"""#30 後半: アカウント削除のパージと保持期限の削除（DB 統合テスト。Auth・Storage は偽物）。"""

import asyncio
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alter_discord_bot import delivery
from alter_domain import account_deletion, account_purge
from alter_domain import discord_delivery as dd
from alter_domain import invitations as inv
from alter_domain.activity import notify_conversation_completed
from alter_domain.auth_admin import AuthIdentity, AuthUser
from sqlalchemy import Connection, Engine, text

GUILD = "200000000000000021"
CHANNEL = "300000000000000021"
THREAD = "600000000000000021"
NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)


@dataclass
class FakeAuth:
    deleted: list[uuid.UUID] = field(default_factory=list)
    fail: bool = False

    def delete_user(self, user_id: uuid.UUID) -> None:
        if self.fail:
            raise account_purge.ExternalUnavailable("auth_http_500")
        self.deleted.append(user_id)


@dataclass
class FakeStorage:
    purged: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    fail: bool = False

    def purge_prefix(self, bucket: str, owner: uuid.UUID) -> int:
        if self.fail:
            raise account_purge.ExternalUnavailable("storage_http_500")
        self.purged.append((bucket, owner))
        return 0


@dataclass
class World:
    owner: uuid.UUID
    agent: uuid.UUID
    partner: uuid.UUID
    partner_agent: uuid.UUID
    conversation: uuid.UUID


@pytest.fixture
def world(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[World]:
    owner, agent = _person(engine, created_users, "AI1")
    partner, partner_agent = _person(engine, created_users, "AI2")
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.discord_guilds (id, name, admin_discord_user_id) "
                "values (:g, '鯖', '400000000000000021')"
            ),
            {"g": GUILD},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, 'ai', '400000000000000021')"
            ),
            {"c": CHANNEL, "g": GUILD},
        )
        conversation = _conversation(conn, agent, partner_agent)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id, status, "
                "discord_thread_id, discord_message_id) values (:c, :ch, 'sent', :t, :t)"
            ),
            {"c": conversation, "ch": CHANNEL, "t": THREAD},
        )
        conn.execute(
            text(
                "insert into public.memories (agent_id, source_conversation_id, text, importance, "
                "dedupe_key, status) values (:a, :c, '記憶', 3, :k, 'active'), "
                "(:b, :c, '相手の記憶', 3, :k, 'active')"
            ),
            {"a": agent, "b": partner_agent, "c": conversation, "k": f"k-{uuid.uuid4()}"},
        )
        # 費用台帳: 確定した課金と、解放されていない保留
        for kind, key in (("charge", "c"), ("reserve", "r")):
            conn.execute(
                text(
                    "insert into public.usage_records (agent_id, conversation_id, provider, model, "
                    "input_tokens, output_tokens, estimated_usd, actual_usd, kind, "
                    "idempotency_key) values (:a, :c, 'anthropic', 'm', 1, 1, 0.01, :actual, "
                    ":kind, :key)"
                ),
                {
                    "a": agent,
                    "c": conversation,
                    "kind": kind,
                    "actual": 0.01 if kind == "charge" else None,
                    "key": f"{key}-{uuid.uuid4()}",
                },
            )
        conn.execute(
            text(
                "insert into public.audit_events (actor_profile_id, action, resource_type, "
                "resource_id, metadata) values "
                "(:o, 'agent.update', 'agent', :a, '{}'), "
                "(:p, 'agent.block', 'agent', :pa, cast(:meta as jsonb)), "
                "(:p, 'agent.update', 'agent', :pa, '{\"result\": \"success\"}')"
            ),
            {
                "o": owner,
                "a": str(agent),
                "p": partner,
                "pa": str(partner_agent),
                "meta": f'{{"note": "{agent}"}}',
            },
        )
        _lock_and_request(conn, owner)
    yield World(owner, agent, partner, partner_agent, conversation)
    with engine.begin() as conn:
        # 消えずに残ったテストの台帳（保留）を先に片付ける（削除ガードは保留のある会話を守る）
        conn.execute(
            text(
                "delete from public.usage_records where agent_id = any(:a) "
                "or conversation_id = :c or agent_id is null"
            ),
            {"a": [agent, partner_agent], "c": conversation},
        )
        conn.execute(
            text(
                "delete from public.conversations where id in (select conversation_id from "
                "public.conversation_participants where agent_id = any(:a))"
            ),
            {"a": [agent, partner_agent]},
        )
        conn.execute(text("delete from public.discord_cleanup_tasks"))
        conn.execute(text("delete from public.discord_channels where guild_id = :g"), {"g": GUILD})
        conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": GUILD})
        conn.execute(
            text("delete from public.background_jobs where kind = :k"),
            {"k": account_deletion.PURGE_JOB_KIND},
        )
        conn.execute(
            text("delete from public.deletion_requests where profile_id = any(:p)"),
            {"p": [owner, partner]},
        )
        conn.execute(
            text(
                "delete from public.audit_events where resource_id = 'redacted' "
                "or actor_profile_id = any(:p)"
            ),  # fmt: skip
            {"p": [owner, partner]},
        )


def _person(engine: Engine, users: list[uuid.UUID], name: str) -> tuple[uuid.UUID, uuid.UUID]:
    sub, agent = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(
            conn, AuthUser(sub, (AuthIdentity("google", f"g-{sub.hex[:10]}"),)), operator="t"
        )
        conn.execute(
            text("insert into public.profiles (id, display_name) values (:id, '本名')"), {"id": sub}
        )
        conn.execute(
            text("insert into public.agents (id, owner_id, name) values (:id, :o, :n)"),
            {"id": agent, "o": sub, "n": name},
        )
        conn.execute(
            text("insert into public.agent_settings (agent_id, daily_budget_usd) values (:a, 0.5)"),
            {"a": agent},
        )
    users.append(sub)
    return sub, agent


def _conversation(conn: Connection, a: uuid.UUID, b: uuid.UUID) -> uuid.UUID:
    cid = uuid.UUID(
        str(
            conn.execute(
                text("insert into public.conversations (status) values ('completed') returning id")
            ).scalar_one()
        )
    )
    for ordinal, agent in ((1, a), (2, b)):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, true, true)"
            ),
            {"c": cid, "a": agent, "o": ordinal},
        )
    conn.execute(
        text(
            "insert into public.messages (conversation_id, agent_id, turn_no, content, "
            "moderation_status) values (:c, :a, 1, '本文', 'allowed')"
        ),
        {"c": cid, "a": a},
    )
    notify_conversation_completed(conn, cid)
    return cid


def _lock_and_request(conn: Connection, owner: uuid.UUID) -> None:
    conn.execute(
        text("select 1 from public.invitations where auth_user_id = :p for update"), {"p": owner}
    )
    conn.execute(text("select 1 from public.profiles where id = :p for update"), {"p": owner})
    account_deletion.request_deletion(conn, owner)


def _make_due(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("update public.background_jobs set run_at = :t where kind = :k"),
            {"t": NOW - timedelta(minutes=1), "k": account_deletion.PURGE_JOB_KIND},
        )


def _one(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.begin() as conn:
        return conn.execute(text(sql), params).first()


def _count(engine: Engine, sql: str, **params: Any) -> int:
    with engine.begin() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


# -- 1 件の削除 ----------------------------------------------------------------------------


def test_purge_removes_owner_data_and_shared_conversation(engine: Engine, world: World) -> None:
    auth, storage = FakeAuth(), FakeStorage()
    _make_due(engine)
    job = account_purge.run_one(engine, auth, storage, NOW)
    assert job is not None and job.profile_id == world.owner

    # 共有会話は双方から消える（本文・参加者・記憶・通知も CASCADE）
    assert _count(engine, "select count(*) from public.conversations where id = :c",
                  c=world.conversation) == 0  # fmt: skip
    assert _count(engine, "select count(*) from public.memories where source_conversation_id "
                  "= :c", c=world.conversation) == 0  # fmt: skip
    assert _count(engine, "select count(*) from public.notifications where conversation_id = :c",
                  c=world.conversation) == 0  # fmt: skip
    # 本人のプロフィール・AI・招待は消え、相手は残る
    assert _count(engine, "select count(*) from public.profiles where id = :p", p=world.owner) == 0
    assert _count(engine, "select count(*) from public.agents where id = :a", a=world.agent) == 0
    assert _count(engine, "select count(*) from public.invitations where auth_user_id = :p",
                  p=world.owner) == 0  # fmt: skip
    assert _count(engine, "select count(*) from public.agents where id = :a",
                  a=world.partner_agent) == 1  # fmt: skip
    # 費用台帳は保留を解放してから匿名化して残す
    ledger = _one(
        engine,
        "select count(*) filter (where kind = 'release') as released, "
        "count(*) filter (where agent_id is not null) as linked from public.usage_records "
        "where agent_id is null or agent_id = :a",
        a=world.agent,
    )
    assert ledger.released == 1 and ledger.linked == 0
    # 監査は本人を辿れない形にする（相手の無関係な記録は残す）
    audit = _one(
        engine,
        "select count(*) filter (where actor_profile_id = :o or resource_id = :a "
        "or metadata::text like '%' || :a || '%') as linked, "
        "count(*) filter (where actor_profile_id = :p and metadata ? 'result') as kept "
        "from public.audit_events",
        o=world.owner,
        a=str(world.agent),
        p=world.partner,
    )
    assert audit.linked == 0 and audit.kept == 1
    # Discord のスレッド ID だけを退避し、外部の削除を呼ぶ
    assert _one(engine, "select thread_id, channel_id, status from public.discord_cleanup_tasks"
                ) == (THREAD, CHANNEL, "pending")  # fmt: skip
    assert storage.purged == [("agent-icons", world.owner), ("profile-private", world.owner)]
    assert auth.deleted == [world.owner]
    req = _one(
        engine,
        "select r.status, r.completed_at, r.error_code, j.status as job from "
        "public.deletion_requests r join public.background_jobs j "
        "on cast(j.payload ->> 'deletion_request_id' as uuid) = r.id where r.profile_id = :p",
        p=world.owner,
    )
    assert (req.status, req.completed_at, req.error_code, req.job) == (
        "completed",
        NOW,
        None,
        "completed",
    )
    # 次の実行では何もしない
    assert account_purge.run_one(engine, auth, storage, NOW) is None


def test_unsettled_llm_call_blocks_and_retries_later(engine: Engine, world: World) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.llm_runs (id, agent_id, conversation_id, purpose, status, "
                "reserved_usd) values (:id, :a, :c, 'conversation', 'unknown', 0.01)"
            ),
            {"id": uuid.uuid4(), "a": world.partner_agent, "c": world.conversation},
        )
    _make_due(engine)
    auth, storage = FakeAuth(), FakeStorage()
    account_purge.run_one(engine, auth, storage, NOW)
    # 何も消さず、外部も呼ばず、理由を残して後で再試行する
    assert _count(engine, "select count(*) from public.profiles where id = :p", p=world.owner) == 1
    assert not auth.deleted and not storage.purged
    row = _one(
        engine,
        "select r.status, r.error_code, j.status as job, j.run_at, j.attempts "
        "from public.deletion_requests r join public.background_jobs j "
        "on cast(j.payload ->> 'deletion_request_id' as uuid) = r.id where r.profile_id = :p",
        p=world.owner,
    )
    assert (row.status, row.error_code, row.job, row.attempts) == (
        "running",
        "llm_unsettled",
        "retry_wait",
        1,
    )
    assert row.run_at == NOW + timedelta(minutes=2)
    assert account_purge.run_one(engine, auth, storage, NOW) is None  # まだ時間前
    with engine.begin() as conn:
        conn.execute(text("delete from public.llm_runs where agent_id = :a"),
                     {"a": world.partner_agent})  # fmt: skip
    account_purge.run_one(engine, auth, storage, NOW + timedelta(minutes=3))
    assert auth.deleted == [world.owner]


def test_owner_suggestion_call_in_flight_also_blocks(engine: Engine, world: World) -> None:
    # 会話に結び付かない本人の提案呼び出しも、結果が確定するまで消さない
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.llm_runs (id, agent_id, purpose, status, reserved_usd) "
                "values (:id, :a, 'suggestion', 'generating', 0.01)"
            ),
            {"id": uuid.uuid4(), "a": world.agent},
        )
    _make_due(engine)
    auth = FakeAuth()
    account_purge.run_one(engine, auth, FakeStorage(), NOW)
    assert not auth.deleted
    assert _one(engine, "select error_code from public.deletion_requests where profile_id = :p",
                p=world.owner).error_code == "llm_unsettled"  # fmt: skip
    with engine.begin() as conn:
        conn.execute(text("delete from public.llm_runs where agent_id = :a"), {"a": world.agent})


def test_external_failure_resumes_without_redoing_harm(engine: Engine, world: World) -> None:
    _make_due(engine)
    auth, storage = FakeAuth(fail=True), FakeStorage()
    account_purge.run_one(engine, auth, storage, NOW)
    # DB の削除は確定済み。Auth の失敗で完了にしない
    assert _count(engine, "select count(*) from public.profiles where id = :p", p=world.owner) == 0
    assert _one(engine, "select status, error_code from public.deletion_requests "
                "where profile_id = :p", p=world.owner) == ("running", "auth_http_500")  # fmt: skip
    assert _count(engine, "select count(*) from public.invitations where auth_user_id = :p",
                  p=world.owner) == 1  # fmt: skip
    # 再開すると DB はもう空でも通り、Auth を削除して完了する
    auth.fail = False
    account_purge.run_one(engine, auth, storage, NOW + timedelta(minutes=5))
    assert auth.deleted == [world.owner]
    assert _one(engine, "select status from public.deletion_requests where profile_id = :p",
                p=world.owner).status == "completed"  # fmt: skip


def test_gives_up_after_max_attempts(engine: Engine, world: World) -> None:
    storage = FakeStorage(fail=True)
    now = NOW
    _make_due(engine)
    for _ in range(account_purge.MAX_ATTEMPTS):
        job = account_purge.run_one(engine, FakeAuth(), storage, now)
        assert job is not None
        now += timedelta(days=1)
    row = _one(engine, "select status, error_code from public.deletion_requests "
               "where profile_id = :p", p=world.owner)  # fmt: skip
    assert (row.status, row.error_code) == ("failed", "storage_http_500")
    assert account_purge.run_one(engine, FakeAuth(), FakeStorage(), now) is None


def test_expired_lease_is_reclaimed_and_concurrent_claims_differ(
    engine: Engine, world: World
) -> None:
    _make_due(engine)
    with engine.connect() as first, engine.connect() as second:
        with first.begin():
            job = account_purge.claim_job(first, NOW)
            assert job is not None
            with second.begin():
                assert account_purge.claim_job(second, NOW) is None
    # 実行中のまま止まっても、リースが切れたら取り直せる
    with engine.begin() as conn:
        assert account_purge.claim_job(conn, NOW + timedelta(minutes=5)) is None
        again = account_purge.claim_job(conn, NOW + timedelta(minutes=16))
    assert again is not None and again.attempts == 2


def test_refuses_profile_without_deletion_request(engine: Engine, world: World) -> None:
    with engine.begin() as conn, pytest.raises(account_purge.PurgeBlocked):
        account_purge.purge_database(conn, world.partner)


def test_sending_post_blocks_until_thread_id_is_saved(engine: Engine, world: World) -> None:
    # Bot がスレッドを作っている途中（sending・ID 未保存）にパージすると取りこぼすので待つ
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.discord_deliveries set status = 'sending', "
                "discord_thread_id = null, discord_message_id = null where conversation_id = :c"
            ),
            {"c": world.conversation},
        )
    _make_due(engine)
    auth = FakeAuth()
    account_purge.run_one(engine, auth, FakeStorage(), NOW)
    assert not auth.deleted
    assert _count(engine, "select count(*) from public.conversations where id = :c",
                  c=world.conversation) == 1  # fmt: skip
    assert _one(engine, "select error_code from public.deletion_requests where profile_id = :p",
                p=world.owner).error_code == "discord_sending"  # fmt: skip
    # Bot が ID を保存して送り終えたあとの再試行で、スレッド ID を退避して消す
    delivery_id = _one(engine, "select id from public.discord_deliveries where conversation_id "
                       "= :c", c=world.conversation).id  # fmt: skip
    with engine.begin() as conn:
        dd.record_thread(conn, delivery_id, THREAD)
        dd.mark_post_sent(conn, delivery_id, THREAD, THREAD)
    account_purge.run_one(engine, auth, FakeStorage(), NOW + timedelta(minutes=3))
    assert auth.deleted == [world.owner]
    assert _one(engine, "select thread_id from public.discord_cleanup_tasks").thread_id == THREAD


def test_purge_waits_for_a_claim_in_progress(engine: Engine, world: World) -> None:
    # Bot の取り出し（pending → sending）の途中なら、確定を待ってから sending として読み直す
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.discord_deliveries set status = 'pending', "
                "discord_thread_id = null, discord_message_id = null where conversation_id = :c"
            ),
            {"c": world.conversation},
        )
    result: list[BaseException | None] = []

    def purge() -> None:
        try:
            with engine.begin() as conn:
                account_purge.purge_database(conn, world.owner)
            result.append(None)
        except BaseException as exc:  # noqa: BLE001
            result.append(exc)

    with engine.connect() as bot, bot.begin():
        bot.execute(
            text("select id from public.discord_deliveries where conversation_id = :c for update"),
            {"c": world.conversation},
        )
        worker = threading.Thread(target=purge)
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive()  # Bot のロックを待っている
        bot.execute(
            text(
                "update public.discord_deliveries set status = 'sending' where conversation_id = :c"
            ),
            {"c": world.conversation},
        )
    worker.join(10)
    assert isinstance(result[0], account_purge.PurgeBlocked)
    assert result[0].code == "discord_sending"


def test_stale_run_cannot_overwrite_a_completed_job(engine: Engine, world: World) -> None:
    _make_due(engine)
    with engine.begin() as conn:
        stale = account_purge.claim_job(conn, NOW)
    assert stale is not None
    # 古い実行が止まっている間にリースが切れ、別の実行が取り直して完了する
    auth = FakeAuth()
    fresh = account_purge.run_one(engine, auth, FakeStorage(), NOW + timedelta(minutes=16))
    assert fresh is not None and fresh.lease_token != stale.lease_token
    assert auth.deleted == [world.owner]
    # 古い実行の失敗・完了は書かれない
    with engine.begin() as conn:
        account_purge.mark_failed(conn, stale, "internal_error", NOW + timedelta(minutes=17))
    with engine.begin() as conn, pytest.raises(account_purge.LeaseLost):
        account_purge.complete(conn, stale, NOW + timedelta(minutes=17))
    row = _one(
        engine,
        "select r.status, r.error_code, j.status as job, j.last_error from "
        "public.deletion_requests r join public.background_jobs j "
        "on cast(j.payload ->> 'deletion_request_id' as uuid) = r.id where r.profile_id = :p",
        p=world.owner,
    )
    assert tuple(row) == ("completed", None, "completed", None)


def test_conversation_jobs_with_owner_ids_are_deleted(engine: Engine, world: World) -> None:
    other_agent = uuid.uuid4()
    with engine.begin() as conn:
        linked = conn.execute(
            text(
                "insert into public.background_jobs (kind, dedupe_key, payload, status) values "
                "('conversation.run', :k, cast(:p as jsonb), 'completed') returning id"
            ),
            {
                "k": f"2026-11-19:3:{world.agent}:{world.partner_agent}",
                "p": f'{{"conversation_id": "{world.conversation}", "seed": 1}}',
            },
        ).scalar_one()
        conn.execute(
            text("update public.conversations set job_id = :j where id = :c"),
            {"j": linked, "c": world.conversation},
        )
        # 会話がすでに消えていて job_id で辿れないもの、相手だけのもの
        conn.execute(
            text(
                "insert into public.background_jobs (kind, dedupe_key, payload, status) values "
                "('conversation.run', :orphan, '{}', 'cancelled'), "
                "('conversation.run', :other, '{}', 'completed')"
            ),
            {
                "orphan": f"2026-11-18:1:{world.agent}:{other_agent}",
                "other": f"2026-11-18:1:{world.partner_agent}:{other_agent}",
            },
        )
    _make_due(engine)
    account_purge.run_one(engine, FakeAuth(), FakeStorage(), NOW)
    keys = [
        r.dedupe_key
        for r in _all(
            engine, "select dedupe_key from public.background_jobs where kind = 'conversation.run'"
        )  # fmt: skip
    ]
    assert keys == [f"2026-11-18:1:{world.partner_agent}:{other_agent}"]
    with engine.begin() as conn:
        conn.execute(text("delete from public.background_jobs where kind = 'conversation.run'"))


def _all(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.begin() as conn:
        return list(conn.execute(text(sql), params))


# -- 保持期限 ------------------------------------------------------------------------------


def test_retention_purge_uses_30_days_from_each_start(engine: Engine, world: World) -> None:
    auth, storage = FakeAuth(), FakeStorage()
    _make_due(engine)
    account_purge.run_one(engine, auth, storage, NOW)
    with engine.begin() as conn:
        conn.execute(
            text("update public.discord_cleanup_tasks set status = 'forbidden', completed_at = :t"),
            {"t": NOW},
        )
        # 相手の招待も同じ時刻に失効（29 日後は残し、31 日後に消す）
        conn.execute(
            text(
                "update public.invitations set status = 'revoked', revoked_at = :t "
                "where auth_user_id = :p"
            ),
            {"t": NOW, "p": world.partner},
        )
    with engine.begin() as conn:
        counts = account_purge.purge_expired(conn, NOW + timedelta(days=29))
    assert counts["deletion_requests"] == 0 and counts["discord_cleanup_tasks"] == 0
    assert counts["invitations"] == 0
    with engine.begin() as conn:
        counts = account_purge.purge_expired(conn, NOW + timedelta(days=31))
    assert counts["deletion_requests"] == 1 and counts["background_jobs"] == 1
    assert counts["discord_cleanup_tasks"] == 1 and counts["invitations"] == 1
    assert _count(engine, "select count(*) from public.deletion_requests where profile_id = :p",
                  p=world.owner) == 0  # fmt: skip


# -- Bot のスレッド削除 ----------------------------------------------------------------------


class _Thread:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error
        self.deleted = False

    async def delete(self) -> None:
        if self.error is not None:
            raise self.error
        self.deleted = True


class _Bot:
    web_url = "https://alter.example"

    def __init__(self, engine: Engine, error: BaseException | None = None) -> None:
        self.engine = engine
        self.thread = _Thread(error)

    async def in_transaction(self, work: Callable[[Connection], Any]) -> Any:
        def go() -> Any:
            with self.engine.begin() as conn:
                return work(conn)

        return await asyncio.to_thread(go)

    def get_channel(self, _: int) -> Any:
        return self.thread

    async def fetch_channel(self, _: int) -> Any:
        raise AssertionError

    def get_user(self, _: int) -> Any:
        raise AssertionError

    async def fetch_user(self, _: int) -> Any:
        raise AssertionError


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "x"


def _http(cls: type[Exception], status: int) -> Exception:
    return cls(_Resp(status), {"code": 50013, "message": "m"})


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, "deleted"),
        ("forbidden", "forbidden"),
        ("not_found", "not_found"),
    ],
)
def test_bot_tries_thread_deletion_and_records_result(
    engine: Engine, world: World, error: str | None, expected: str
) -> None:
    import discord

    _make_due(engine)
    account_purge.run_one(engine, FakeAuth(), FakeStorage(), NOW)
    exc = {
        None: None,
        "forbidden": _http(discord.Forbidden, 403),
        "not_found": _http(discord.NotFound, 404),
    }[error]
    bot = _Bot(engine, exc)
    assert asyncio.run(delivery.process_once(bot)) == 1
    row = _one(engine, "select status, completed_at is not null as done, last_error "
               "from public.discord_cleanup_tasks")  # fmt: skip
    assert (row.status, row.done) == (expected, True)
    assert bot.thread.deleted is (error is None)


def test_thread_deletion_retries_unknown_failures_then_gives_up(
    engine: Engine, world: World
) -> None:
    _make_due(engine)
    account_purge.run_one(engine, FakeAuth(), FakeStorage(), NOW)
    bot = _Bot(engine, TimeoutError())
    for attempt in range(1, dd.MAX_ATTEMPTS + 1):
        with engine.begin() as conn:
            conn.execute(text("update public.discord_cleanup_tasks set next_attempt_at = now()"))
        asyncio.run(delivery.process_once(bot))
        row = _one(engine, "select status, attempts from public.discord_cleanup_tasks")
        assert row.attempts == attempt
        if attempt < dd.MAX_ATTEMPTS:
            assert row.status == "pending"
    assert _one(engine, "select status from public.discord_cleanup_tasks").status == "failed"
