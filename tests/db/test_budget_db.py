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


@pytest.mark.parametrize("stop_during_generation", [False, True])
def test_shared_conversation_execution_uses_only_approved_profile_and_session_reservation(
    engine, ai_pair, stop_during_generation
):
    import json

    import httpx
    from alter_domain.agent_models import AgentDraftInput
    from alter_domain.agents import save_revision
    from alter_domain.budget import held, release_session
    from alter_domain.llm_execution import LLMExecution
    from alter_domain.llm_selection import MODEL, model_price, session_reservation
    from alter_domain.profile_crypto import ProfileCipher
    from alter_domain.settings import Settings
    from alter_domain.sonnet import SonnetClient

    with engine.begin() as conn:
        conv = new_conversation(conn)
        for slot, agent in enumerate(ai_pair, 1):
            conn.execute(
                text(
                    "insert into public.conversation_participants "
                    "(conversation_id,agent_id,ordinal,discord_consent,web_consent) "
                    "values (:c,:a,:s,false,true)"
                ),
                {"c": conv, "a": agent, "s": slot},
            )
            conn.execute(
                text("update public.agent_settings set autonomy_enabled=true where agent_id=:a"),
                {"a": agent},
            )
        draft = AgentDraftInput(
            name="AI",
            personality="UNAPPROVED_SENTINEL",
            values_text="対話",
            interests=["本"],
            speaking_style="丁寧",
            interaction_policy="尊重",
            shareable_info="公開",
            restrictions="推測禁止",
            private_info="PRIVATE_SENTINEL",
        )
        save_revision(
            conn,
            ai_pair[0],
            2,
            draft,
            ProfileCipher("eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="),
        )
        reserve(
            conn,
            ai_pair[0],
            conv,
            model_price(),
            "session-safe",
            session_reservation(),
            Decimal("10"),
        )
    requests = []
    counted = []

    def handler(req):
        assert engine.pool.checkedout() == 0
        body = json.loads(req.content)
        assert "PRIVATE_SENTINEL" not in req.content.decode()
        assert "UNAPPROVED_SENTINEL" not in req.content.decode()
        if req.url.path.endswith("count_tokens"):
            counted.append(body)
            return httpx.Response(200, json={"input_tokens": 100})
        requests.append(body)
        if stop_during_generation:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "update public.agent_settings set autonomy_enabled=false where agent_id=:a"
                    ),
                    {"a": ai_pair[1]},
                )
        verdict = "allowed" in body["output_config"]["format"]["schema"]["properties"]
        value = (
            {"allowed": True}
            if verdict
            else {"utterance": "本について話しましょう", "should_end": False}
        )
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(value)}],
            },
        )

    settings = Settings(
        _env_file=None, alter_mode="live", llm_enabled=True, anthropic_api_key="synthetic-key"
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        service = LLMExecution(engine, SonnetClient(settings, http), engine.begin, Decimal("10"))
        if stop_during_generation:
            from alter_domain.llm import UnsafeOutput

            with pytest.raises(UnsafeOutput):
                service.conversation_turn(ai_pair[0], conv, [])
            assert len(requests) == len(counted) == 1
            with engine.begin() as conn:
                release_session(conn, ai_pair[0], conv, model_price())
            return
        for _ in range(4):
            result = service.conversation_turn(ai_pair[0], conv, ["最近の本"], ["本の話題"])
            assert result.utterance == "本について話しましょう"
        before = len(requests)
        with pytest.raises(BudgetExceeded):
            service.conversation_turn(ai_pair[0], conv, [])
        assert len(requests) == before == 8
    with engine.begin() as conn:
        assert held(conn, ai_pair[0], conv) == session_reservation() - 8 * model_price().cost(
            100, 10
        )
        assert (
            conn.execute(
                text(
                    "select count(*) from public.usage_records where agent_id=:a "
                    "and conversation_id=:c and kind='reserve'"
                ),
                {"a": ai_pair[0], "c": conv},
            ).scalar_one()
            == 1
        )
        from alter_domain.budget import UnsettledBudget

        # Simulate unresolved bookkeeping: finally must not discard its session hold.
        conn.execute(
            text(
                "update public.llm_runs set status='unknown' where id=("
                "select id from public.llm_runs where agent_id=:a limit 1)"
            ),
            {"a": ai_pair[0]},
        )
        with pytest.raises(UnsettledBudget):
            release_session(conn, ai_pair[0], conv, model_price())
        conn.execute(
            text("update public.llm_runs set status='succeeded' where agent_id=:a"),
            {"a": ai_pair[0]},
        )
        release_session(conn, ai_pair[0], conv, model_price())


def test_crashed_execution_is_durable_and_never_automatically_retried(engine, ai_pair):
    from alter_domain.agent_models import ProfileInput
    from alter_domain.llm import FakeLLM
    from alter_domain.llm_execution import LLMExecution, UnsettledLLMCall, suggestion_prompt
    from alter_domain.llm_selection import call_reservation

    class Crash(BaseException):
        pass

    class CrashingLLM(FakeLLM):
        calls = 0

        def complete(self, call):
            self.calls += 1
            raise Crash()

    llm = CrashingLLM()
    service = LLMExecution(engine, llm, engine.begin, Decimal("10"))
    with pytest.raises(Crash):
        service.execute(ai_pair[0], suggestion_prompt({"personality": "穏やか"}), ProfileInput)
    with engine.begin() as conn:
        assert exposure(conn, agent=ai_pair[0]) == call_reservation()
        assert (
            conn.execute(
                text("select status from public.llm_runs where agent_id=:a"), {"a": ai_pair[0]}
            ).scalar_one()
            == "generating"
        )
    with pytest.raises(UnsettledLLMCall):
        service.execute(ai_pair[0], suggestion_prompt({"personality": "穏やか"}), ProfileInput)
    assert llm.calls == 1
    from alter_domain.budget import UnsettledBudget, release_for_deletion

    with engine.begin() as conn, pytest.raises(UnsettledBudget):
        release_for_deletion(conn, agent=ai_pair[0])
