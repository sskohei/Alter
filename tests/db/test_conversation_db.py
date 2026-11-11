import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from alter_domain.budget import exposure
from alter_domain.conversation import LeaseLost, assert_lease, claim, renew, run_one
from alter_domain.llm import (
    BaselineModerator,
    FakeLLM,
    LLMResponse,
    TemporaryLLMError,
    UnknownLLMOutcome,
)
from alter_domain.llm_contract import ModelPrice
from alter_domain.scheduling import schedule
from sqlalchemy import Engine, text


class PricedFake(FakeLLM):
    price = ModelPrice(
        provider="fake",
        model="priced-test",
        input_per_million=Decimal(".01"),
        output_per_million=Decimal(".01"),
    )


def queued(engine):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    assert len(schedule(engine, now, PricedFake.price, Decimal("10"))) == 1
    return now


def test_parallel_worker_saves_eight_turns_once_and_releases_budget(
    engine: Engine, ai_pair: list[UUID]
):
    queued(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_one(engine, PricedFake(), BaselineModerator()), range(2)))
    with engine.begin() as conn:
        rows = conn.execute(
            text("select agent_id,turn_no from public.messages order by turn_no")
        ).all()
        assert [r.turn_no for r in rows] == list(range(1, 9))
        assert all(rows[i].agent_id != rows[i + 1].agent_id for i in range(7))
        assert (
            conn.execute(
                text("select status from public.background_jobs where kind='conversation'")
            ).scalar_one()
            == "completed"
        )
        assert (
            conn.execute(
                text("select count(*) from public.conversation_events where kind='completed'")
            ).scalar_one()
            == 1
        )
        assert exposure(conn) == Decimal(".000008")
    assert not run_one(engine, PricedFake(), BaselineModerator())


def test_temporary_error_resumes_without_holding_db_transaction(
    engine: Engine, ai_pair: list[UUID]
):
    now = queued(engine)

    class Flaky(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            # A separate writer can acquire row locks while the LLM is executing.
            with engine.begin() as conn:
                conn.execute(text("set local lock_timeout='1s'"))
                conn.execute(text("select agent_id from public.agent_settings for update"))
            if self.calls == 1:
                raise TemporaryLLMError(1)
            return super().generate(messages)

    client = Flaky()
    run_one(engine, client, BaselineModerator(), clock=lambda: now)
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("select status from public.background_jobs where kind='conversation'")
            ).scalar_one()
            == "retry_wait"
        )
    run_one(engine, client, BaselineModerator(), clock=lambda: now + timedelta(seconds=45))
    assert client.calls == 9


@pytest.mark.parametrize("scenario", ["unknown", "stop", "consent", "unsafe"])
def test_no_more_calls_after_unknown_stop_consent_or_unsafe(
    engine: Engine, ai_pair: list[UUID], scenario
):
    queued(engine)
    stopped = threading.Event()

    class Controlled(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            if scenario == "unknown":
                raise UnknownLLMOutcome()
            if scenario == "stop":
                stopped.set()
            if scenario == "consent":
                with engine.begin() as conn:
                    conn.execute(text("update public.agent_settings set autonomy_enabled=false"))
            if scenario == "unsafe":
                return LLMResponse(
                    output=json.dumps({"utterance": "x@example.com", "should_end": False}),
                    input_tokens=10,
                    output_tokens=10,
                )
            return super().generate(messages)

    client = Controlled()
    assert run_one(engine, client, BaselineModerator(), stopped=stopped)
    assert client.calls == 1
    with engine.begin() as conn:
        assert conn.execute(text("select count(*) from public.messages")).scalar_one() == 0
        assert exposure(conn) == (Decimal(".10") if scenario == "unknown" else Decimal(".000001"))


def test_expired_worker_is_fenced_and_started_call_is_never_redispatched(
    engine: Engine, ai_pair: list[UUID]
):
    now = queued(engine)
    with engine.begin() as conn:
        lease = claim(conn, now)
        assert lease is not None
        conn.execute(
            text(
                "insert into public.llm_invocations (conversation_id,turn_no,status) "
                "values (:id,1,'started')"
            ),
            {"id": lease.conversation},
        )
    future = now + timedelta(seconds=91)
    assert not renew(engine, lease, future)

    class Counted(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            return super().generate(messages)

    client = Counted()
    run_one(engine, client, BaselineModerator(), clock=lambda: future)
    assert client.calls == 0
    with engine.begin() as conn:
        with pytest.raises(LeaseLost):
            assert_lease(conn, lease, future)
        assert (
            conn.execute(
                text("select last_error from public.background_jobs where id=:id"),
                {"id": lease.job},
            ).scalar_one()
            == "unknown_llm_outcome"
        )
