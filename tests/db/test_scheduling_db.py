from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from alter_domain.llm import FakeLLM
from alter_domain.scheduling import schedule
from sqlalchemy import Engine, text


def test_parallel_cron_reserves_once_and_initial_off_excludes(engine: Engine, ai_pair: list[UUID]):
    now = datetime.now(UTC)
    assert schedule(engine, now, FakeLLM.price, Decimal("10")) == []
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: schedule(engine, now, FakeLLM.price, Decimal("10")), range(2))
        )
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
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        conn.execute(
            text(
                "insert into public.agent_blocks (blocker_agent_id,blocked_agent_id) values (:a,:b)"
            ),
            {"a": ai_pair[0], "b": ai_pair[1]},
        )
    assert schedule(engine, now, FakeLLM.price, Decimal("10")) == []
    with engine.begin() as conn:
        conn.execute(text("delete from public.agent_blocks"))
    assert schedule(engine, now, FakeLLM.price, Decimal(".05")) == []
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("select count(*) from public.background_jobs where kind='conversation'")
            ).scalar_one()
            == 0
        )
        assert conn.execute(text("select count(*) from public.usage_records")).scalar_one() == 0
