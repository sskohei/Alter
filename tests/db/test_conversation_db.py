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
    FakeLLM,
    LLMResponse,
    TemporaryLLMError,
    UnknownLLMOutcome,
)
from alter_domain.llm_selection import model_price, session_reservation
from alter_domain.scheduling import schedule
from sqlalchemy import Engine, text


class PricedFake(FakeLLM):
    price = model_price()


def queued(engine):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    assert len(schedule(engine, now, Decimal("10"))) == 1
    return now


def test_parallel_worker_saves_eight_turns_once_and_releases_budget(
    engine: Engine, ai_pair: list[UUID]
):
    queued(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_one(engine, PricedFake()), range(2)))
    with engine.begin() as conn:
        rows = conn.execute(
            text("select agent_id,turn_no from public.messages order by turn_no")
        ).all()
        assert [r.turn_no for r in rows] == list(range(1, 9))
        assert all(rows[i].agent_id != rows[i + 1].agent_id for i in range(7))
        assert (
            conn.execute(
                text(
                    "select status from public.background_jobs where kind='conversation.run' "
                    "and id in (select job_id from public.conversations)"
                )
            ).scalar_one()
            == "completed"
        )
        assert (
            conn.execute(
                text("select count(*) from public.conversation_events where kind='completed'")
            ).scalar_one()
            == 1
        )
        assert exposure(conn) == Decimal(".001920")
        assert (
            conn.execute(
                text(
                    "select count(*) from public.notifications where kind='conversation.completed'"
                )
            ).scalar_one()
            == 2
        )
    assert not run_one(engine, PricedFake())


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
    run_one(engine, client, clock=lambda: now)
    with engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "select status from public.background_jobs where kind='conversation.run' "
                    "and id in (select job_id from public.conversations)"
                )
            ).scalar_one()
            == "retry_wait"
        )
    run_one(engine, client, clock=lambda: now + timedelta(seconds=45))
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
    assert run_one(engine, client, stopped=stopped)
    assert client.calls == 1
    with engine.begin() as conn:
        assert conn.execute(text("select count(*) from public.messages")).scalar_one() == 0
        assert exposure(conn) == (
            2 * session_reservation() if scenario == "unknown" else Decimal(".000120")
        )


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
    run_one(engine, client, clock=lambda: future)
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


def job_state(engine):
    with engine.begin() as conn:
        job = conn.execute(
            text(
                "select status,last_error,run_at from public.background_jobs "
                "where kind='conversation.run' "
                "and id in (select job_id from public.conversations)"
            )
        ).one()
        count = conn.execute(text("select count(*) from public.messages")).scalar_one()
        events = conn.execute(text("select count(*) from public.conversation_events")).scalar_one()
        return job, count, events


@pytest.mark.parametrize(
    "scenario", ["should_end", "incomplete", "rejected", "permanent", "moderation_retry"]
)
def test_terminal_results_and_no_regeneration_after_billed_call(engine, ai_pair, scenario):
    from alter_domain.sonnet import LLMUnavailable

    queued(engine)

    class Controlled(PricedFake):
        calls = 0

        def complete(self, call):
            self.calls += 1
            if scenario == "permanent":
                raise LLMUnavailable()
            if call.body["schema"] == "SafetyVerdict":
                if scenario == "moderation_retry":
                    raise TemporaryLLMError()
                if scenario == "rejected":
                    return LLMResponse(
                        output='{"allowed":false}', input_tokens=10, output_tokens=10
                    )
            if scenario == "should_end" and call.body["schema"] == "Utterance":
                return LLMResponse(
                    output='{"utterance":"またお話ししましょう。","should_end":true}',
                    input_tokens=10,
                    output_tokens=10,
                )
            result = super().complete(call)
            if scenario == "incomplete":
                return result.model_copy(update={"completed": False})
            return result

    client = Controlled()
    run_one(engine, client)
    state, count, events = job_state(engine)
    assert state.status == ("completed" if scenario == "should_end" else "failed")
    assert count == (1 if scenario == "should_end" else 0)
    assert events == 1
    assert not run_one(engine, client)
    assert client.calls == (1 if scenario in ("incomplete", "permanent") else 2)
    with engine.begin() as conn:
        assert exposure(conn) == Decimal(".000120") * (
            0 if scenario == "permanent" else 2 if scenario in ("should_end", "rejected") else 1
        )


@pytest.mark.parametrize(
    "scenario", ["block", "profile", "version", "deletion", "budget", "time", "stop"]
)
def test_changes_during_generation_block_moderation_and_persistence(engine, ai_pair, scenario):
    now = queued(engine)
    current = [now]
    stopped = threading.Event()

    class Changed(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            if scenario == "stop":
                stopped.set()
            elif scenario == "time":
                current[0] += timedelta(seconds=301)
                # Keep the lease valid to distinguish wall time from expired lease behavior.
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "update public.background_jobs set lease_until=:until "
                            "where status='running' and id in "
                            "(select job_id from public.conversations)"
                        ),
                        {"until": current[0] + timedelta(seconds=90)},
                    )
            else:
                with engine.begin() as conn:
                    if scenario == "block":
                        conn.execute(
                            text(
                                "insert into public.agent_blocks "
                                "(blocker_agent_id,blocked_agent_id) values (:a,:b)"
                            ),
                            {"a": ai_pair[0], "b": ai_pair[1]},
                        )
                    elif scenario == "profile":
                        conn.execute(
                            text(
                                "update public.agents set status='paused', "
                                "approved_profile_version=null"
                            )
                        )
                    elif scenario == "version":
                        from alter_domain.agent_models import AgentDraftInput, ProfileInput
                        from alter_domain.agents import approve, get_profile, save_revision

                        aid = ai_pair[0]
                        profile = get_profile(conn, aid, 1)
                        draft = AgentDraftInput(
                            name="AI", **profile.model_dump(include=set(ProfileInput.model_fields))
                        )
                        save_revision(conn, aid, 2, draft, None)
                        owner = conn.execute(
                            text("select owner_id from public.agents where id=:id"), {"id": aid}
                        ).scalar_one()
                        approve(conn, owner, aid, 2)
                    elif scenario == "deletion":
                        conn.execute(text("update public.profiles set deleted_at=now()"))
                    else:
                        conn.execute(text("update public.agent_settings set daily_budget_usd=.001"))
            return super().generate(messages)

        def complete(self, call):
            assert call.body["schema"] != "SafetyVerdict"
            return super().complete(call)

    client = Changed()
    run_one(engine, client, clock=lambda: current[0], stopped=stopped)
    state, count, events = job_state(engine)
    assert state.status == ("completed" if scenario in ("budget", "time") else "cancelled")
    assert count == 0 and events == 1
    assert client.calls == 1
    with engine.begin() as conn:
        assert exposure(conn) == Decimal(".000120")


def test_retry_after_backoff_limit_and_time_cap(engine, ai_pair):
    now = queued(engine)

    class Unbilled(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            raise TemporaryLLMError(180)

    client = Unbilled()
    run_one(engine, client, clock=lambda: now)
    state, _, _ = job_state(engine)
    assert now + timedelta(seconds=180) <= state.run_at <= now + timedelta(seconds=190)
    assert not run_one(engine, client, clock=lambda: now + timedelta(seconds=179))
    run_one(engine, client, clock=lambda: state.run_at)
    state, _, _ = job_state(engine)
    run_one(engine, client, clock=lambda: state.run_at)
    assert job_state(engine)[0].status == "completed"  # Retry wait is included in five minutes.
    assert client.calls == 2


def test_retries_stop_after_three_even_for_reclaimed_job(engine, ai_pair):
    now = queued(engine)
    with engine.begin() as conn:
        conn.execute(text("update public.background_jobs set attempts=3"))

    class Unbilled(PricedFake):
        def generate(self, messages):
            raise TemporaryLLMError()

    run_one(engine, Unbilled(), clock=lambda: now)
    state, count, events = job_state(engine)
    assert state.status == "failed" and state.last_error == "retry_limit"
    assert count == 0 and events == 1


def test_crash_between_committed_turns_resumes_without_duplicate(engine, ai_pair, monkeypatch):
    import alter_domain.conversation as worker

    now = queued(engine)
    real_prepare = worker.prepare
    prepares = [0]

    class Crash(BaseException):
        pass

    def crash_after_one(*args, **kwargs):
        prepares[0] += 1
        if prepares[0] == 2:
            raise Crash()
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(worker, "prepare", crash_after_one)
    with pytest.raises(Crash):
        run_one(engine, PricedFake(), clock=lambda: now)
    assert job_state(engine)[1] == 1
    monkeypatch.setattr(worker, "prepare", real_prepare)
    run_one(engine, PricedFake(), clock=lambda: now + timedelta(seconds=91))
    assert job_state(engine)[0].status == "completed"
    assert job_state(engine)[1:] == (8, 1)
    with engine.begin() as conn:
        assert conn.execute(text("select count(*) from public.llm_runs")).scalar_one() == 8


def test_live_sonnet_http_mock_uses_generation_and_independent_review(engine, ai_pair):
    import httpx
    from alter_domain.llm_selection import MODEL
    from alter_domain.settings import Settings
    from alter_domain.sonnet import SonnetClient

    queued(engine)
    requests = []

    def respond(request):
        data = json.loads(request.content)
        requests.append((request.url.path, data))
        # External wait occurs outside all Worker/LLM transactions.
        with engine.begin() as conn:
            conn.execute(text("set local lock_timeout='1s'"))
            conn.execute(text("select agent_id from public.agent_settings for update"))
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 20})
        review = data["max_tokens"] == 16
        output = (
            '{"allowed":true}'
            if review
            else '{"utterance":"またお話ししましょう。","should_end":true}'
        )
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": output}],
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    settings = Settings(
        _env_file=None, alter_mode="live", llm_enabled=True, anthropic_api_key="test-only"
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = SonnetClient(settings, http)
        run_one(engine, client)
        assert client.before_request is None
    assert [path for path, _ in requests] == ["/v1/messages/count_tokens", "/v1/messages"] * 2
    assert job_state(engine)[0].status == "completed"
    assert job_state(engine)[1:] == (1, 1)


def test_full_guarded_conversation_fits_cloud_database_roundtrip_budget(engine, ai_pair):
    import httpx
    from alter_domain.llm_selection import MODEL
    from alter_domain.settings import Settings
    from alter_domain.sonnet import SonnetClient
    from sqlalchemy import event

    queued(engine)
    requests = []
    roundtrips = []

    def count(*args):
        roundtrips.append(1)

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 20})
        review = json.loads(request.content)["max_tokens"] == 16
        output = (
            '{"allowed":true}' if review else '{"utterance":"読書が好きです。","should_end":false}'
        )
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": output}],
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    settings = Settings(
        _env_file=None, alter_mode="live", llm_enabled=True, anthropic_api_key="synthetic"
    )
    event.listen(engine, "before_cursor_execute", count)
    event.listen(engine, "begin", count)
    event.listen(engine, "commit", count)
    event.listen(engine, "rollback", count)
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            assert run_one(engine, SonnetClient(settings, http))
    finally:
        event.remove(engine, "before_cursor_execute", count)
        event.remove(engine, "begin", count)
        event.remove(engine, "commit", count)
        event.remove(engine, "rollback", count)
    assert job_state(engine)[1] == 8
    assert requests == ["/v1/messages/count_tokens", "/v1/messages"] * 16
    # Measured cloud RTT is ~0.3s. Leave at least 60s of the 5-minute limit for the LLM.
    assert len(roundtrips) * 0.3 < 240, f"Too many cloud roundtrips: {len(roundtrips)}"


def test_lease_reclaimed_during_provider_call_rejects_stale_output_but_records_cost(
    engine, ai_pair
):
    now = queued(engine)

    class Reclaimed(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "update public.background_jobs set lease_until=:past "
                        "where status='running' and id in "
                        "(select job_id from public.conversations)"
                    ),
                    {"past": now - timedelta(seconds=1)},
                )
            # A different Worker reclaims the lease while the old provider call is outstanding.
            run_one(engine, PricedFake(), clock=lambda: now)
            return super().generate(messages)

    client = Reclaimed()
    run_one(engine, client, clock=lambda: now)
    assert client.calls == 1
    state, count, events = job_state(engine)
    assert state.status == "failed" and state.last_error == "unknown_llm_outcome"
    assert count == 0 and events == 1
    with engine.begin() as conn:
        assert conn.execute(
            text("select sum(actual_usd) from public.usage_records where kind='charge'")
        ).scalar_one() == Decimal(".000120")
        assert conn.execute(
            text("select cost_usd from public.conversations")
        ).scalar_one() == Decimal(".000120")
        assert exposure(conn) == 2 * session_reservation()


@pytest.mark.parametrize("scenario", ["stop", "profile"])
def test_real_adapter_stops_after_token_count_without_dispatch(engine, ai_pair, scenario):
    import httpx
    from alter_domain.settings import Settings
    from alter_domain.sonnet import SonnetClient

    queued(engine)
    stopped = threading.Event()
    paths = []

    def respond(request):
        paths.append(request.url.path)
        assert request.url.path.endswith("count_tokens")
        if scenario == "stop":
            stopped.set()
        else:
            with engine.begin() as conn:
                conn.execute(text("update public.agents set status='paused'"))
        return httpx.Response(200, json={"input_tokens": 20})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = SonnetClient(
            Settings(
                _env_file=None, alter_mode="live", llm_enabled=True, anthropic_api_key="test-only"
            ),
            http,
        )
        run_one(engine, client, stopped=stopped)
    assert paths == ["/v1/messages/count_tokens"]
    assert job_state(engine)[0].status == "cancelled"
    assert job_state(engine)[1:] == (0, 1)
    with engine.begin() as conn:
        assert exposure(conn) == 0


def test_api_cancelled_queue_releases_holds_and_emits_one_event_without_llm(engine, ai_pair):
    from alter_domain.interaction_settings import cancel_queued_interactions

    queued(engine)
    with engine.begin() as conn:
        assert cancel_queued_interactions(conn, ai_pair[0]) == 1

    class NoCalls(PricedFake):
        def require_enabled(self):
            raise AssertionError("cancel cleanup must not invoke LLM")

    assert run_one(engine, NoCalls())
    assert not run_one(engine, NoCalls())
    assert job_state(engine)[0].status == "cancelled"
    assert job_state(engine)[1:] == (0, 1)
    with engine.begin() as conn:
        assert exposure(conn) == 0


@pytest.mark.parametrize("scenario", ["stop", "consent"])
def test_revocation_during_independent_review_discards_output_and_charges_both_calls(
    engine, ai_pair, scenario
):
    queued(engine)
    stopped = threading.Event()

    class ReviewRevoked(PricedFake):
        calls = 0

        def complete(self, call):
            self.calls += 1
            result = super().complete(call)
            if call.body["schema"] == "SafetyVerdict":
                if scenario == "stop":
                    stopped.set()
                else:
                    with engine.begin() as conn:
                        conn.execute(
                            text("update public.agent_settings set autonomy_enabled=false")
                        )
            return result

    client = ReviewRevoked()
    run_one(engine, client, stopped=stopped)
    assert client.calls == 2
    assert job_state(engine)[0].status == "cancelled"
    assert job_state(engine)[1:] == (0, 1)
    with engine.begin() as conn:
        assert exposure(conn) == Decimal(".000240")


def test_orphaned_job_does_not_block_valid_conversation(engine, ai_pair):
    from uuid import uuid4

    now = queued(engine)
    orphan = uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into public.background_jobs "
                    "(id,kind,dedupe_key,payload,run_at) "
                    "values (:id,'conversation.run',:key,'{}',:at)"
                ),
                {"id": orphan, "key": str(orphan), "at": now - timedelta(days=1)},
            )
        run_one(engine, PricedFake())
        assert job_state(engine)[0].status == "completed"
        assert job_state(engine)[1:] == (8, 1)
    finally:
        with engine.begin() as conn:
            conn.execute(text("delete from public.background_jobs where id=:id"), {"id": orphan})


@pytest.mark.parametrize("fail_at", [1, 3])
def test_deletion_request_during_retry_wait_stops_the_resumed_conversation(
    engine: Engine, ai_pair: list[UUID], fail_at: int
):
    """#32 削除と再試行: 一時障害で再試行待ちの間に削除要求が来たら、再開しても LLM を呼ばない。"""
    from alter_domain.account_deletion import request_deletion

    now = queued(engine)

    class Flaky(PricedFake):
        calls = 0

        def generate(self, messages):
            self.calls += 1
            if self.calls == fail_at:
                raise TemporaryLLMError(1)
            return super().generate(messages)

    def counts(conn):
        return conn.execute(
            text(
                "select (select count(*) from public.messages), j.status from "
                "public.background_jobs j join public.conversations c on c.job_id = j.id"
            )
        ).one()

    client = Flaky()
    run_one(engine, client, clock=lambda: now)
    with engine.begin() as conn:
        saved, status = counts(conn)
        assert (saved, status) == (fail_at - 1, "retry_wait")
        owner = conn.execute(
            text("select owner_id from public.agents where id = :a"), {"a": ai_pair[0]}
        ).scalar_one()
    try:
        with engine.begin() as conn:
            request_deletion(conn, owner)
        calls = client.calls
        # 再試行の時刻を過ぎて Worker が取り直しても、発言前の再確認で止まる
        while run_one(engine, client, clock=lambda: now + timedelta(seconds=45)):
            pass
        assert client.calls == calls
        with engine.begin() as conn:
            saved, status = counts(conn)
            assert saved == fail_at - 1
            assert status not in {"queued", "retry_wait", "running"}
            charged = conn.execute(
                text(
                    "select coalesce(sum(actual_usd), 0) from public.usage_records "
                    "where kind = 'charge'"
                )
            ).scalar_one()
            # 予約は残らず、使った分だけが費用になる
            assert exposure(conn) == charged
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "delete from public.background_jobs where kind = 'account.purge' and "
                    "payload ->> 'deletion_request_id' in (select id::text from "
                    "public.deletion_requests where profile_id = :o)"
                ),
                {"o": owner},
            )
            conn.execute(
                text("delete from public.deletion_requests where profile_id = :o"), {"o": owner}
            )
