import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alter_domain.budget import held
from alter_domain.conversation import run_one
from alter_domain.llm import FakeLLM, LLMResponse, UnknownLLMOutcome
from alter_domain.llm_selection import model_price
from alter_domain.memory import enrich_pending
from alter_domain.scheduling import schedule
from alter_domain.summary import summarize_one
from sqlalchemy import text


class Client(FakeLLM):
    price = model_price()

    def __init__(self):
        self.calls = []

    def complete(self, call):
        self.calls.append(call.body["schema"])
        return super().complete(call)


def completed(engine):
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    assert schedule(engine, datetime.now(UTC), Decimal("10"))
    assert run_one(engine, Client())
    assert enrich_pending(engine) == 1
    with engine.connect() as conn:
        assert conn.execute(text("select summary from public.conversations")).scalar_one() is None
        return conn.execute(text("select id from public.conversations")).scalar_one()


def test_summary_is_a_reviewed_paragraph_and_is_charged_once(engine, ai_pair):
    conversation = completed(engine)
    client = Client()
    assert summarize_one(engine, client, global_limit=Decimal("10"))
    assert client.calls == ["SummaryText", "SafetyVerdict"]
    assert not summarize_one(engine, client, global_limit=Decimal("10"))
    with engine.connect() as conn:
        summary = conn.execute(text("select summary from public.conversations")).scalar_one()
        assert "読書" in summary and "結論には至らなかった" in summary
        assert "会話中の発言:" not in summary and len(summary) <= 180
        assert (
            conn.execute(
                text(
                    "select count(*) from public.llm_runs where purpose='summary' "
                    "and status='succeeded'"
                )
            ).scalar_one()
            == 1
        )
        assert all(held(conn, aid, conversation) == 0 for aid in ai_pair)
        assert (
            conn.execute(text("select cost_usd from public.conversations")).scalar_one()
            == conn.execute(
                text("select sum(actual_usd) from public.usage_records where kind='charge'")
            ).scalar_one()
        )


def test_concurrent_summary_workers_cannot_double_bill(engine, ai_pair):
    completed(engine)
    clients = [Client(), Client()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda c: summarize_one(engine, c, global_limit=Decimal("10")), clients)
        )
    assert sum(results) == 1
    assert sum(len(c.calls) for c in clients) == 2


def test_summary_can_use_the_other_participants_remaining_budget(engine, ai_pair):
    completed(engine)
    first, second = sorted(ai_pair)
    with engine.begin() as conn:
        conn.execute(
            text("update public.agent_settings set max_session_usd=.001 where agent_id=:id"),
            {"id": first},
        )
    assert summarize_one(engine, Client(), global_limit=Decimal("10"))
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select agent_id from public.llm_runs where purpose='summary'")
            ).scalar_one()
            == second
        )


def test_real_adapter_rechecks_lowered_budget_after_token_count_before_billing(engine, ai_pair):
    import httpx
    from alter_domain.settings import Settings
    from alter_domain.sonnet import SonnetClient

    completed(engine)
    paths = []

    def respond(request):
        paths.append(request.url.path)
        assert request.url.path.endswith("count_tokens")
        with engine.begin() as conn:
            conn.execute(
                text("update public.agent_settings set daily_budget_usd=0,max_session_usd=0")
            )
        return httpx.Response(200, json={"input_tokens": 20})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        settings = Settings(
            _env_file=None, alter_mode="live", llm_enabled=True, anthropic_api_key="synthetic"
        )
        assert summarize_one(engine, SonnetClient(settings, http), global_limit=Decimal("10"))
    assert paths == ["/v1/messages/count_tokens"]
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select status from public.llm_runs where purpose='summary'")
            ).scalar_one()
            == "failed"
        )
        assert (
            conn.execute(
                text(
                    "select count(*) from public.usage_records u join public.llm_runs r "
                    "on starts_with(u.idempotency_key,'llm:'||r.id::text||':') "
                    "where r.purpose='summary' and u.kind='charge'"
                )
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("condition", ["budget", "off", "blocked", "forgotten"])
def test_no_generation_without_budget_or_current_permission(engine, ai_pair, condition):
    conversation = completed(engine)
    with engine.begin() as conn:
        if condition == "off":
            conn.execute(text("update public.agent_settings set autonomy_enabled=false"))
        elif condition == "blocked":
            conn.execute(
                text(
                    "insert into public.agent_blocks (blocker_agent_id,blocked_agent_id) "
                    "values (:a,:b)"
                ),
                {"a": ai_pair[0], "b": ai_pair[1]},
            )
        elif condition == "forgotten":
            conn.execute(
                text(
                    "insert into public.memory_source_suppressions "
                    "(agent_id,source_conversation_id) values (:a,:c)"
                ),
                {"a": ai_pair[0], "c": conversation},
            )
    client = Client()
    assert not summarize_one(
        engine, client, global_limit=Decimal("0") if condition == "budget" else Decimal("10")
    )
    assert client.calls == []


def test_unknown_summary_outcome_retains_reservation_and_never_retries(engine, ai_pair):
    conversation = completed(engine)

    class Unknown(Client):
        def complete(self, call):
            self.calls.append(call.body["schema"])
            raise UnknownLLMOutcome()

    client = Unknown()
    assert summarize_one(engine, client, global_limit=Decimal("10"))
    assert not summarize_one(engine, client, global_limit=Decimal("10"))
    assert len(client.calls) == 1
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select status from public.llm_runs where purpose='summary'")
            ).scalar_one()
            == "unknown"
        )
        assert sum(held(conn, aid, conversation) for aid in ai_pair) > 0
        assert conn.execute(text("select summary from public.conversations")).scalar_one() is None


def test_revoked_consent_during_generation_discards_summary_but_records_known_cost(engine, ai_pair):
    conversation = completed(engine)

    class Revoke(Client):
        def complete(self, call):
            result = super().complete(call)
            with engine.begin() as conn:
                conn.execute(text("update public.agent_settings set autonomy_enabled=false"))
            return result

    client = Revoke()
    summarize_one(engine, client, global_limit=Decimal("10"))
    assert client.calls == ["SummaryText"]
    with engine.connect() as conn:
        assert conn.execute(text("select summary from public.conversations")).scalar_one() is None
        assert all(held(conn, aid, conversation) == 0 for aid in ai_pair)
        assert (
            conn.execute(
                text("select status from public.llm_runs where purpose='summary'")
            ).scalar_one()
            == "failed"
        )


def test_legacy_list_summary_is_replaced_without_rewriting_messages(engine, ai_pair):
    completed(engine)
    with engine.begin() as conn:
        before = conn.execute(text("select id,content from public.messages order by turn_no")).all()
        conn.execute(text("update public.conversations set summary='AI1の会話中の発言: 古い列挙'"))
    assert summarize_one(engine, Client(), global_limit=Decimal("10"))
    with engine.connect() as conn:
        assert (
            conn.execute(text("select id,content from public.messages order by turn_no")).all()
            == before
        )
        assert (
            "古い列挙"
            not in conn.execute(text("select summary from public.conversations")).scalar_one()
        )


def test_independent_review_rejection_never_publishes_summary(engine, ai_pair):
    conversation = completed(engine)

    class Reject(Client):
        def complete(self, call):
            if call.body["schema"] == "SafetyVerdict":
                self.calls.append("SafetyVerdict")
                return LLMResponse(
                    output=json.dumps({"allowed": False}), input_tokens=10, output_tokens=10
                )
            return super().complete(call)

    assert summarize_one(engine, Reject(), global_limit=Decimal("10"))
    with engine.connect() as conn:
        assert conn.execute(text("select summary from public.conversations")).scalar_one() is None
        assert all(held(conn, aid, conversation) == 0 for aid in ai_pair)
