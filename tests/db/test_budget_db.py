from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID

import pytest
from alter_domain.budget import BudgetExceeded, LedgerConflict, exposure, reserve
from alter_domain.llm import FakeLLM
from sqlalchemy import Engine, text


def test_parallel_reservation_never_exceeds_global_cap(engine: Engine, ai_pair: list[UUID]):
    def attempt(aid):
        try:
            with engine.begin() as conn:
                reserve(conn, aid, None, FakeLLM.price, str(aid), Decimal(".05"), Decimal(".05"))
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ai_pair)) == [False, True]
    with engine.begin() as conn:
        assert exposure(conn) == Decimal(".05")
        # A hold from yesterday still counts; unknown external outcomes are not auto-released.
        conn.execute(text("update public.usage_records set created_at=now()-interval '1 day'"))
        assert exposure(conn) == Decimal(".05")
        with pytest.raises(BudgetExceeded):
            reserve(
                conn, ai_pair[0], None, FakeLLM.price, "another", Decimal(".05"), Decimal(".05")
            )


def test_ledger_replay_requires_same_provider_model_and_amount(engine: Engine, ai_pair: list[UUID]):
    with engine.begin() as conn:
        for _ in range(2):
            reserve(
                conn, ai_pair[0], None, FakeLLM.price, "replay-key", Decimal(".05"), Decimal("10")
            )
        assert exposure(conn) == Decimal(".05")
        with pytest.raises(LedgerConflict):
            reserve(
                conn,
                ai_pair[0],
                None,
                FakeLLM.price.model_copy(update={"model": "different"}),
                "replay-key",
                Decimal(".05"),
                Decimal("10"),
            )


def new_conversation(conn):
    return conn.execute(
        text("insert into public.conversations default values returning id")
    ).scalar_one()


def test_session_limit_includes_multiple_holds_and_previous_day_charges(engine, ai_pair):
    from alter_domain.budget import consume, session_exposure

    agent = ai_pair[0]
    with engine.begin() as conn:
        conv = new_conversation(conn)
        reserve(conn, agent, conv, FakeLLM.price, "first", Decimal(".03"), Decimal("10"))
        consume(
            conn,
            agent,
            conv,
            FakeLLM.price,
            "turn",
            Decimal(".02"),
            input_tokens=1,
            output_tokens=1,
        )
        conn.execute(text("update public.usage_records set created_at=now()-interval '1 day'"))
        # .01 held + .02 charged + .02 new = exactly .05, even across UTC days.
        reserve(conn, agent, conv, FakeLLM.price, "second", Decimal(".02"), Decimal("10"))
        assert session_exposure(conn, agent, conv) == Decimal(".05")
        with pytest.raises(BudgetExceeded):
            reserve(conn, agent, conv, FakeLLM.price, "overflow", Decimal(".000001"), Decimal("10"))
        reserve(conn, agent, conv, FakeLLM.price, "second", Decimal(".02"), Decimal("10"))
        from alter_domain.budget import release_for_deletion

        release_for_deletion(conn, conversation=conv)
        conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})


def test_parallel_reservations_respect_combined_session_limit(engine, ai_pair):
    from alter_domain.budget import release_for_deletion

    with engine.begin() as conn:
        conv = new_conversation(conn)

    def attempt(key):
        try:
            with engine.begin() as conn:
                reserve(conn, ai_pair[0], conv, FakeLLM.price, key, Decimal(".03"), Decimal("10"))
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["parallel-a", "parallel-b"])) == [False, True]
    with engine.begin() as conn:
        release_for_deletion(conn, conversation=conv)
        conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})


def test_conversation_deletion_releases_both_agents_but_preserves_charges(engine, ai_pair):
    from alter_domain.budget import consume, release_for_deletion
    from sqlalchemy.exc import IntegrityError

    with engine.begin() as conn:
        conv = new_conversation(conn)
        other = new_conversation(conn)
        for agent in ai_pair:
            reserve(conn, agent, conv, FakeLLM.price, str(agent), Decimal(".05"), Decimal("10"))
        reserve(conn, ai_pair[0], other, FakeLLM.price, "unrelated", Decimal(".01"), Decimal("10"))
        consume(
            conn,
            ai_pair[0],
            conv,
            FakeLLM.price,
            "spent",
            Decimal(".01"),
            input_tokens=1,
            output_tokens=1,
        )
        # Unknown outcomes keep their holds and prevent losing the original references.
        with pytest.raises(IntegrityError, match="release outstanding budget holds"):
            with conn.begin_nested():
                conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})
        assert exposure(conn) == Decimal(".11")
        # Caller has now stopped generation and reconciled provider results.
        release_for_deletion(conn, conversation=conv)
        count = conn.execute(text("select count(*) from public.usage_records")).scalar_one()
        release_for_deletion(conn, conversation=conv)
        assert conn.execute(text("select count(*) from public.usage_records")).scalar_one() == count
        conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})
        assert exposure(conn) == Decimal(".02")  # .01 actual + unrelated .01 hold
        assert exposure(conn, agent=ai_pair[1]) == 0
        release_for_deletion(conn, conversation=other)
        conn.execute(text("delete from public.conversations where id=:id"), {"id": other})


def test_agent_deletion_releases_nonconversation_holds_and_keeps_anonymous_cost(engine, ai_pair):
    from alter_domain.budget import consume, release_for_deletion
    from sqlalchemy.exc import IntegrityError

    agent = ai_pair[0]
    with engine.begin() as conn:
        conv = new_conversation(conn)
        reserve(conn, agent, conv, FakeLLM.price, "session", Decimal(".05"), Decimal("10"))
        reserve(conn, agent, None, FakeLLM.price, "suggest", Decimal(".02"), Decimal("10"))
        consume(
            conn,
            agent,
            conv,
            FakeLLM.price,
            "actual",
            Decimal(".01"),
            input_tokens=1,
            output_tokens=1,
        )
        with pytest.raises(IntegrityError, match="release outstanding budget holds"):
            with conn.begin_nested():
                conn.execute(text("delete from public.agents where id=:id"), {"id": agent})
        release_for_deletion(conn, agent=agent)
        assert exposure(conn) == Decimal(".01")
        # Restore identities after assertion so the shared test fixture can clean up.
        with conn.begin_nested() as savepoint:
            conn.execute(text("delete from public.agents where id=:id"), {"id": agent})
            assert exposure(conn) == Decimal(".01")
            assert (
                conn.execute(
                    text("select count(*) from public.usage_records where agent_id is not null")
                ).scalar_one()
                == 0
            )
            savepoint.rollback()
        conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})


def test_deletion_and_release_rollback_together(engine, ai_pair):
    from alter_domain.budget import held, release_for_deletion

    with engine.begin() as conn:
        conv = new_conversation(conn)
        reserve(conn, ai_pair[0], conv, FakeLLM.price, "rollback", Decimal(".05"), Decimal("10"))
        with conn.begin_nested() as savepoint:
            release_for_deletion(conn, conversation=conv)
            conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})
            savepoint.rollback()
        assert held(conn, ai_pair[0], conv) == Decimal(".05")
        release_for_deletion(conn, conversation=conv)
        conn.execute(text("delete from public.conversations where id=:id"), {"id": conv})


def test_deletion_release_requires_target(engine):
    from alter_domain.budget import release_for_deletion

    with engine.begin() as conn, pytest.raises(ValueError):
        release_for_deletion(conn)
