from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from queue import Queue
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import pytest
from alter_domain.budget import lock_budget, release_session, reserve
from alter_domain.interaction import pair_allowed
from alter_domain.llm_selection import model_price, session_reservation
from alter_domain.scheduling import schedule, select_due
from sqlalchemy import Engine, text

TABLES = ("background_jobs", "conversations", "conversation_participants", "usage_records")


def row_counts(engine):
    with engine.begin() as conn:
        return tuple(
            conn.execute(text(f"select count(*) from public.{table}")).scalar_one()
            for table in TABLES
        )


def test_parallel_cron_reserves_once_and_initial_off_excludes(engine: Engine, ai_pair: list[UUID]):
    now = datetime.now(UTC)
    assert schedule(engine, now, Decimal("10")) == []
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule(engine, now, Decimal("10")), range(2)))
    assert sum(len(r) for r in results) == 1
    with engine.begin() as conn:
        assert (
            conn.execute(text("select count(*) from public.conversation_participants")).scalar_one()
            == 2
        )
        assert (
            conn.execute(
                text("select count(*) from public.usage_records where kind='reserve'")
            ).scalar_one()
            == 2
        )
        assert not conn.execute(
            text("select bool_or(discord_consent) from public.conversation_participants")
        ).scalar_one()


def test_one_sided_block_and_insufficient_global_budget_leave_no_job(
    engine: Engine, ai_pair: list[UUID]
):
    now = datetime.now(UTC)
    before = row_counts(engine)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        conn.execute(
            text(
                "insert into public.agent_blocks (blocker_agent_id,blocked_agent_id) values (:a,:b)"
            ),
            {"a": ai_pair[0], "b": ai_pair[1]},
        )
    assert schedule(engine, now, Decimal("10")) == []
    with engine.begin() as conn:
        conn.execute(text("delete from public.agent_blocks"))
    assert schedule(engine, now, Decimal(".05")) == []
    assert row_counts(engine) == before


# Change just one participant: consent and eligibility must hold on both sides.
@pytest.mark.parametrize(
    "mutation",
    [
        "update public.agent_settings set autonomy_enabled=false where agent_id=:id",
        "update public.agent_settings set frequency_per_day=0 where agent_id=:id",
        "update public.agents set status='paused' where id=:id",
        "update public.agents set status='deleted' where id=:id",
        "update public.agents set status='draft',approved_profile_version=null where id=:id",
        "update public.profiles set deleted_at=now() "
        "where id=(select owner_id from public.agents where id=:id)",
        "update public.invitations set status='revoked',revoked_at=now() "
        "where auth_user_id=(select owner_id from public.agents where id=:id)",
        "insert into public.deletion_requests (profile_id) "
        "select owner_id from public.agents where id=:id",
        "update public.agent_settings set max_session_usd=.049791 where agent_id=:id",
        "update public.agent_settings set daily_budget_usd=.049791, max_session_usd=.049791 "
        "where agent_id=:id",
    ],
)
def test_ineligible_participant_leaves_no_reservation(engine, ai_pair, mutation):
    before = row_counts(engine)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        conn.execute(text(mutation), {"id": ai_pair[0]})
        assert not pair_allowed(conn, (ai_pair[0], ai_pair[0]), datetime.now(UTC))
    try:
        assert schedule(engine, datetime.now(UTC), Decimal("1")) == []
        assert row_counts(engine) == before
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "delete from public.deletion_requests where profile_id in "
                    "(select owner_id from public.agents where id=:id)"
                ),
                {"id": ai_pair[0]},
            )


@pytest.mark.parametrize("status", ["reserved", "generating", "generated", "moderating", "unknown"])
def test_unsettled_llm_call_excludes_agent(engine, ai_pair, status):
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        conn.execute(
            text(
                "insert into public.llm_runs (id,agent_id,purpose,status,reserved_usd) "
                "values (:run,:agent,'suggestion',:status,.012448)"
            ),
            {"run": uuid4(), "agent": ai_pair[0], "status": status},
        )
    try:
        assert schedule(engine, datetime.now(UTC), Decimal("1")) == []
    finally:
        with engine.begin() as conn:
            conn.execute(text("delete from public.llm_runs where agent_id=:id"), {"id": ai_pair[0]})


def test_daily_exposure_includes_existing_holds(engine, ai_pair):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        for i in range(4):
            reserve(
                conn, ai_pair[0], None, model_price(), f"old-hold:{i}", Decimal(".04"), Decimal("1")
            )
        conn.execute(text("update public.usage_records set created_at=now()-interval '1 day'"))
    before = row_counts(engine)
    assert schedule(engine, now, Decimal("1")) == []
    assert row_counts(engine) == before


def test_session_snapshot_dedupe_next_slot_and_utc_daily_limit(engine, ai_pair):
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    with engine.begin() as conn:
        conn.execute(
            text("update public.agent_settings set autonomy_enabled=true,frequency_per_day=2")
        )
    jobs = schedule(engine, now, Decimal("1"), seed=19)
    assert len(jobs) == 1
    with engine.begin() as conn:
        job = conn.execute(
            text("select dedupe_key,payload,run_at from public.background_jobs where id=:id"),
            {"id": jobs[0]},
        ).one()
        assert job.dedupe_key == f"{now.date()}:48:{min(ai_pair)}:{max(ai_pair)}"
        assert job.payload["seed"] == 19
        assert job.run_at == now
        conversation = conn.execute(
            text("select id,profile_versions from public.conversations where job_id=:id"),
            {"id": jobs[0]},
        ).one()
        assert conversation.profile_versions == {str(a): 1 for a in ai_pair}
        assert str(conversation.id) == job.payload["conversation_id"]
        reservations = conn.execute(
            text(
                "select provider,model,estimated_usd from public.usage_records where kind='reserve'"
            )
        ).all()
        assert len(reservations) == 2
        assert all(
            (r.provider, r.model, r.estimated_usd)
            == (model_price().provider, model_price().model, session_reservation())
            for r in reservations
        )
    # Queued and running conversations both occupy the one-session limit.
    assert schedule(engine, now + timedelta(minutes=15), Decimal("1")) == []
    with engine.begin() as conn:
        conn.execute(
            text("update public.conversations set status='running' where id=:id"),
            {"id": conversation.id},
        )
    assert schedule(engine, now + timedelta(minutes=15), Decimal("1")) == []
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.conversations set status='completed',ended_at=:now "
                "where id in (select conversation_id from public.conversation_participants "
                "where agent_id=any(:ids))"
            ),
            {"now": now, "ids": ai_pair},
        )
        conn.execute(
            text(
                "update public.background_jobs set status='completed' where id in "
                "(select c.job_id from public.conversations c "
                "join public.conversation_participants p on p.conversation_id=c.id "
                "where p.agent_id=any(:ids))"
            ),
            {"ids": ai_pair},
        )
        for agent in ai_pair:
            release_session(conn, agent, conversation.id, model_price())
    # A terminal job still prevents repeating the same pair in the same UTC slot.
    assert schedule(engine, now, Decimal("1")) == []
    assert len(schedule(engine, now + timedelta(minutes=15), Decimal("1"))) == 1
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.conversations set status='completed',ended_at=:now "
                "where id in (select conversation_id from public.conversation_participants "
                "where agent_id=any(:ids))"
            ),
            {"now": now, "ids": ai_pair},
        )
        conn.execute(
            text(
                "update public.background_jobs set status='completed' where id in "
                "(select c.job_id from public.conversations c "
                "join public.conversation_participants p on p.conversation_id=c.id "
                "where p.agent_id=any(:ids))"
            ),
            {"ids": ai_pair},
        )
    assert schedule(engine, now + timedelta(minutes=30), Decimal("1")) == []
    # The count resets at UTC midnight, even with the previous day's released job history.
    assert len(schedule(engine, now + timedelta(days=1), Decimal("1"))) == 1


def test_live_entry_creates_reservation_and_exits(engine, ai_pair, monkeypatch):
    from alter_scheduler.main import main

    monkeypatch.setenv("ALTER_MODE", "live")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", str(engine.url.render_as_string(hide_password=False)))
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    before = row_counts(engine)
    main()
    assert row_counts(engine) == tuple(n + d for n, d in zip(before, (1, 1, 2, 2), strict=True))


@pytest.mark.parametrize("ai_pair", [4], indirect=True)
def test_batch_reserves_disjoint_pairs_and_falls_back_on_pair_budget(engine, ai_pair):
    agents = sorted(ai_pair)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        # Make every other pair familiar so the first pair ranks highest.
        for a, b in combinations(agents, 2):
            if (a, b) != (agents[0], agents[1]):
                conn.execute(
                    text(
                        "insert into public.agent_relationships "
                        "(agent_low_id,agent_high_id,conversation_count) values (:a,:b,3)"
                    ),
                    {"a": a, "b": b},
                )
        # The highest ranked pair cannot afford the selected session reservation.
        conn.execute(
            text("update public.agent_settings set max_session_usd=.01 where agent_id=:id"),
            {"id": agents[0]},
        )
    assert len(schedule(engine, datetime.now(UTC), Decimal("1"), seed=19)) == 1
    with engine.begin() as conn:
        participants = (
            conn.execute(text("select agent_id from public.conversation_participants"))
            .scalars()
            .all()
        )
        assert agents[0] not in participants
        assert len(set(participants)) == 2
        # Restore the fourth AI so the remaining disjoint pair can also be scheduled.
        conn.execute(text("update public.agent_settings set max_session_usd=.05"))
    assert len(schedule(engine, datetime.now(UTC), Decimal("1"), seed=19)) == 1
    with engine.begin() as conn:
        participants = (
            conn.execute(text("select agent_id from public.conversation_participants"))
            .scalars()
            .all()
        )
        assert sorted(participants) == agents
        assert conn.execute(text("select count(*) from public.usage_records")).scalar_one() == 4


@pytest.mark.parametrize("ai_pair", [4], indirect=True)
def test_parallel_cron_respects_global_cap_across_different_pairs(engine, ai_pair):
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    now = datetime.now(UTC)
    before = row_counts(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: schedule(engine, now, session_reservation() * 2), range(2))
        )
    assert sum(map(len, results)) == 1
    assert row_counts(engine) == tuple(n + d for n, d in zip(before, (1, 1, 2, 2), strict=True))
    with engine.begin() as conn:
        assert (
            conn.execute(text("select sum(estimated_usd) from public.usage_records")).scalar_one()
            == session_reservation() * 2
        )


@pytest.mark.parametrize("consents", [(False, False), (True, False), (False, True), (True, True)])
def test_participants_snapshot_individual_discord_consent(engine, ai_pair, consents):
    expected = dict(zip(ai_pair, consents, strict=True))
    with engine.begin() as conn:
        for agent, consent in expected.items():
            conn.execute(
                text(
                    "update public.agent_settings set autonomy_enabled=true,"
                    "discord_publish=:consent "
                    "where agent_id=:agent"
                ),
                {"agent": agent, "consent": consent},
            )
    assert len(schedule(engine, datetime.now(UTC), Decimal("1"))) == 1
    with engine.begin() as conn:
        # Later settings changes must not rewrite the consent captured at conversation start.
        conn.execute(text("update public.agent_settings set discord_publish=not discord_publish"))
        participants = conn.execute(
            text(
                "select agent_id,discord_consent,web_consent from public.conversation_participants"
            )
        ).all()
        assert {row.agent_id: row.discord_consent for row in participants} == expected
        assert all(row.web_consent for row in participants)


def test_scheduler_waiting_for_api_rows_does_not_hold_budget_lock(engine, ai_pair):
    scheduler_pid = Queue()

    def run_scheduler():
        with engine.begin() as conn:
            conn.execute(text("set local lock_timeout='5s'"))
            scheduler_pid.put(conn.execute(text("select pg_backend_pid()")).scalar_one())
            return select_due(conn, datetime.now(UTC), Decimal("1"))

    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as conn:
            # Reproduce business_connection's authorization locks before budget work.
            conn.execute(text("set local lock_timeout='2s'"))
            owner = conn.execute(
                text("select owner_id from public.agents where id=:agent"), {"agent": ai_pair[0]}
            ).scalar_one()
            conn.execute(
                text("select id from public.invitations where auth_user_id=:owner for update"),
                {"owner": owner},
            ).all()
            conn.execute(
                text("select id from public.profiles where id=:owner for update"), {"owner": owner}
            ).all()
            api_pid = conn.execute(text("select pg_backend_pid()")).scalar_one()
            future = pool.submit(run_scheduler)
            pid = scheduler_pid.get(timeout=5)
            deadline = monotonic() + 5
            while True:
                blockers = conn.execute(
                    text("select pg_blocking_pids(:pid)"), {"pid": pid}
                ).scalar_one()
                if api_pid in blockers:
                    break
                assert monotonic() < deadline, "Scheduler did not wait on API authorization locks"
                Event().wait(0.01)
            # Must succeed while Scheduler waits for our rows: the old order deadlocks here.
            lock_budget(conn)
        assert future.result(timeout=5) is not None
