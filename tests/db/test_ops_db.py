# ruff: noqa: F811  （world は test_discord_delivery_db の fixture を引数名で受け取る）
"""#36: 運用の観測（件数・警告）と、Discord 配送の要確認の照合。"""

import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alter_domain import discord_delivery as dd
from alter_domain import ops
from alter_domain.cli import ops as cli
from alter_domain.settings import Settings
from sqlalchemy import Engine, text
from test_discord_delivery_db import (  # noqa: F401  （fixture の再利用）
    CHANNEL,
    OTHER_CHANNEL,
    Person,
    _conversation,
    _drop_second_guild,
    _post_rows,
    _second_guild,
    run,
    world,
)

KIND = "ops.test"
THREAD = "600000000000000101"
MESSAGE = "500000000000000101"


@pytest.fixture
def cleanup_ops(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text("delete from public.background_jobs where kind = :k"), {"k": KIND})
        conn.execute(
            text(
                "delete from public.audit_events where action in "
                "('discord.delivery_resolve', 'discord.dm_resolve', 'discord.target_choose')"
            )
        )


def _now(engine: Engine) -> datetime:
    with engine.connect() as conn:
        return conn.execute(text("select now()")).scalar_one()


def _snap(engine: Engine, now: datetime | None = None) -> dict[str, Any]:
    with engine.connect() as conn:
        return ops.snapshot(conn, now or _now(engine), max_daily_cost=Decimal("1.00"))


def _held(engine: Engine, world: tuple[Person, Person]) -> uuid.UUID:
    """送信が途中で止まり、要確認（partial）になった投稿を 1 件作る。"""
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_after_completion, cid)
    job = run(engine, dd.claim_next_post)
    assert job is not None
    run(engine, dd.mark_post_failed, job.delivery_id, "needs_review", "partial:TimeoutError")
    return job.delivery_id


def _audits(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "select action, resource_id, metadata from public.audit_events "
                    "where action like 'discord.%' and resource_type like 'discord_%' "
                    "order by created_at"
                )
            )
        )


# -- 観測 -------------------------------------------------------------------------------------


def test_snapshot_counts_waiting_and_expired_jobs(engine: Engine, cleanup_ops: None) -> None:
    now = _now(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.background_jobs (kind, dedupe_key, status, run_at) values "
                "(:k, 'a', 'queued', :old), (:k, 'b', 'retry_wait', :recent), "
                "(:k, 'c', 'queued', :future)"
            ),
            {
                "k": KIND,
                "old": now - timedelta(minutes=45),
                "recent": now - timedelta(minutes=1),
                "future": now + timedelta(hours=1),
            },
        )
        conn.execute(
            text(
                "insert into public.background_jobs (kind, dedupe_key, status, lease_until) "
                "values (:k, 'd', 'running', :expired), (:k, 'e', 'running', :fresh)"
            ),
            {
                "k": KIND,
                "expired": now - timedelta(minutes=10),
                "fresh": now + timedelta(minutes=1),
            },
        )
        conn.execute(
            text(
                "insert into public.background_jobs (kind, dedupe_key, status) "
                "values (:k, 'f', 'failed')"
            ),
            {"k": KIND},
        )
    snap = _snap(engine, now)
    # 予定時刻前のジョブは待ちに数えない。リースは 5 分の猶予を超えたものだけ
    assert snap["jobs"][KIND] == {
        "due": 2,
        "oldest_due_minutes": 45,
        "lease_expired": 1,
        "failed_24h": 1,
        "completed_24h": 0,
    }
    found = ops.alerts(snap)
    assert {f"jobs_waiting:{KIND}", f"lease_expired:{KIND}", f"jobs_failed:{KIND}"} <= set(found)
    # 本文・ID は含めない（数値・日時・固定の文字列だけ）
    assert "dedupe" not in json.dumps(snap)


def test_snapshot_counts_review_and_exposes_no_ids(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    before = _snap(engine)["discord"]
    _held(engine, world)
    snap = _snap(engine)
    assert snap["discord"]["posts"]["needs_review"] == before["posts"]["needs_review"] + 1
    assert "discord_needs_review" in ops.alerts(snap)
    dumped = json.dumps(snap)
    for person in world:
        assert str(person.profile) not in dumped
        assert person.discord_id not in dumped
    assert "旅の話" not in dumped


def test_unsettled_llm_and_budget_are_reported(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    a, _ = world
    before = _snap(engine)
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.llm_runs (id, agent_id, purpose, status, reserved_usd) "
                "values (:id, :a, 'suggestion', 'unknown', 0.01)"
            ),
            {"id": run_id, "a": a.agent},
        )
        conn.execute(
            text(
                "insert into public.usage_records (agent_id, provider, model, input_tokens, "
                "output_tokens, estimated_usd, actual_usd, kind, idempotency_key) "
                "values (:a, 'anthropic', 'm', 100, 20, 0.85, 0.85, 'charge', :k)"
            ),
            {"a": a.agent, "k": f"ops-test-{run_id}"},
        )
    try:
        snap = _snap(engine)
        assert snap["llm_unsettled"]["unknown"] == before["llm_unsettled"]["unknown"] + 1
        assert Decimal(snap["cost"]["charged_today_usd"]) - Decimal(
            before["cost"]["charged_today_usd"]
        ) == Decimal("0.85")
        assert snap["cost"]["input_tokens_today"] - before["cost"]["input_tokens_today"] == 100
        found = ops.alerts(snap)
        assert "llm_unknown_outcome" in found
        assert "budget_near_limit" in found
    finally:
        with engine.begin() as conn:
            conn.execute(text("delete from public.llm_runs where id = :id"), {"id": run_id})
            conn.execute(
                text("delete from public.usage_records where idempotency_key = :k"),
                {"k": f"ops-test-{run_id}"},
            )


def test_deletion_near_deadline_is_reported(engine: Engine, cleanup_ops: None) -> None:
    profile = uuid.uuid4()
    now = _now(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.deletion_requests (profile_id, status, requested_at) "
                "values (:p, 'pending', :at)"
            ),
            {"p": profile, "at": now - timedelta(days=26)},
        )
    try:
        snap = _snap(engine, now)
        assert snap["deletion"]["oldest_open_days"] >= 26
        assert "deletion_near_deadline" in ops.alerts(snap)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("delete from public.deletion_requests where profile_id = :p"), {"p": profile}
            )


# -- 照合 -------------------------------------------------------------------------------------


def test_resolve_post_as_sent_records_ids_and_audit(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    delivery_id = _held(engine, world)
    items = [i for i in run(engine, ops.list_review) if i.id == delivery_id]
    assert len(items) == 1
    assert (items[0].kind, items[0].last_error) == ("post", "partial:TimeoutError")

    with pytest.raises(ops.InvalidResolution):
        run(engine, ops.resolve_post, delivery_id, "sent", operator="sskohei", thread_id=THREAD)
    with pytest.raises(ops.InvalidResolution):
        run(
            engine,
            ops.resolve_post,
            delivery_id,
            "sent",
            operator="sskohei",
            thread_id="abc",
            message_id=MESSAGE,
        )
    run(
        engine,
        ops.resolve_post,
        delivery_id,
        "sent",
        operator="sskohei",
        thread_id=THREAD,
        message_id=MESSAGE,
    )
    cid = run(
        engine,
        lambda c: c.execute(
            text("select conversation_id from public.discord_deliveries where id = :id"),
            {"id": delivery_id},
        ).scalar_one(),
    )
    row = _post_rows(engine, cid)[0]
    assert (row.status, row.discord_thread_id, row.discord_message_id, row.last_error) == (
        "sent",
        THREAD,
        MESSAGE,
        None,
    )
    # 要確認ではなくなった行は二度と書き換えない
    with pytest.raises(ops.ReviewNotFound):
        run(engine, ops.resolve_post, delivery_id, "cancelled", operator="sskohei")
    audits = _audits(engine)
    assert [(a.action, a.resource_id, a.metadata) for a in audits] == [
        (
            "discord.delivery_resolve",
            str(delivery_id),
            {"outcome": "sent", "operator": "sskohei"},
        )
    ]


def test_resolve_post_cancelled_and_dm(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    delivery_id = _held(engine, world)
    run(engine, ops.resolve_post, delivery_id, "cancelled", operator="sskohei")
    status = run(
        engine,
        lambda c: c.execute(
            text("select status, last_error from public.discord_deliveries where id = :id"),
            {"id": delivery_id},
        ).one(),
    )
    assert tuple(status) == ("cancelled", "resolved_cancelled")

    dm = run(engine, dd.claim_next_dm)
    assert dm is not None
    run(engine, dd.mark_dm_failed, dm.delivery_id, "needs_review", "timeout")
    assert any(i.kind == "dm" and i.id == dm.delivery_id for i in run(engine, ops.list_review))
    with pytest.raises(ops.InvalidResolution):
        run(engine, ops.resolve_dm, dm.delivery_id, "sent", operator="sskohei")
    run(engine, ops.resolve_dm, dm.delivery_id, "sent", operator="sskohei", message_id=MESSAGE)
    row = run(
        engine,
        lambda c: c.execute(
            text("select status, discord_message_id from public.discord_dm_deliveries where id=:i"),
            {"i": dm.delivery_id},
        ).one(),
    )
    assert tuple(row) == ("sent", MESSAGE)
    # 未確定（pending）の DM は照合の対象にしない
    other = run(engine, dd.claim_next_dm)
    assert other is not None
    with pytest.raises(ops.ReviewNotFound):
        run(engine, ops.resolve_dm, other.delivery_id, "cancelled", operator="sskohei")


def test_choose_target_releases_one_post_and_is_not_held_again(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    a, b = world
    _second_guild(engine, world)
    try:
        cid = _conversation(engine, a, b)
        assert run(engine, dd.enqueue_conversation_posts, cid) == 2
        with pytest.raises(ops.ReviewNotFound):
            run(engine, ops.choose_target, cid, "300000000000000099", operator="sskohei")
        run(engine, ops.choose_target, cid, OTHER_CHANNEL, operator="sskohei")
        assert {(r.channel_id, r.status, r.last_error) for r in _post_rows(engine, cid)} == {
            (CHANNEL, "cancelled", "not_chosen"),
            (OTHER_CHANNEL, "pending", None),
        }
        # 両方のギルドがまだ条件を満たしていても、選ばなかった投稿先は数えない
        job = run(engine, dd.claim_next_post)
        assert job is not None
        assert (job.conversation_id, job.channel_id) == (cid, OTHER_CHANNEL)
        assert [a.action for a in _audits(engine)] == ["discord.target_choose"]
    finally:
        _drop_second_guild(engine)


def test_choose_target_rejects_channel_that_is_no_longer_eligible(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    a, b = world
    _second_guild(engine, world)
    try:
        cid = _conversation(engine, a, b)
        run(engine, dd.enqueue_conversation_posts, cid)
        with engine.begin() as conn:
            conn.execute(
                text("delete from public.agent_discord_consents where agent_id = :a and guild_id = "
                     "(select guild_id from public.discord_channels where id = :c)"),
                {"a": a.agent, "c": OTHER_CHANNEL},
            )  # fmt: skip
        with pytest.raises(ops.InvalidResolution):
            run(engine, ops.choose_target, cid, OTHER_CHANNEL, operator="sskohei")
        assert {r.status for r in _post_rows(engine, cid)} == {"needs_review"}
    finally:
        _drop_second_guild(engine)


# -- CLI ------------------------------------------------------------------------------------


def _deps(engine: Engine, stdin: str = "") -> tuple[cli.Deps, io.StringIO]:
    out = io.StringIO()
    settings = Settings(_env_file=None, database_url=str(engine.url))  # type: ignore[call-arg]
    return cli.Deps(settings, engine, io.StringIO(stdin), out, datetime.now(UTC)), out


def test_cli_status_exits_3_on_alert(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    _held(engine, world)
    deps, out = _deps(engine)
    code = cli.main(["--env", "local", "--operator", "sskohei", "status", "--json"], deps=deps)
    assert code == cli.EXIT_ALERT
    assert "discord_needs_review" in json.loads(out.getvalue())["alerts"]


def test_cli_resolve_and_target_check(
    engine: Engine, world: tuple[Person, Person], cleanup_ops: None
) -> None:
    delivery_id = _held(engine, world)
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", "--operator", "sskohei", "review-list"], deps=deps) == 0
    assert str(delivery_id) in out.getvalue()
    # ローカルの DB に production として書き込もうとしたら中止する
    deps, out = _deps(engine, "production\n")
    args = ["--operator", "sskohei", "resolve-post", str(delivery_id), "cancelled"]
    assert cli.main(["--env", "production", *args], deps=deps) == cli.EXIT_FAILED
    assert run(engine, ops.list_review)
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", *args], deps=deps) == cli.EXIT_OK
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", *args], deps=deps) == cli.EXIT_FAILED
    assert "not_found" in out.getvalue()
