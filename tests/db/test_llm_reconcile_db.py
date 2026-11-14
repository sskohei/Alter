"""#36: 結果不明の LLM 呼び出しの照合（課金の記録・保留の解放・確定できない条件）。"""

import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alter_domain import budget, llm_reconcile, ops
from alter_domain.cli import ops as cli
from alter_domain.llm_contract import ModelPrice
from alter_domain.llm_selection import call_reservation, model_price, session_reservation
from alter_domain.settings import Settings
from sqlalchemy import Engine, text

PRICE = model_price()
FAKE = ModelPrice(
    provider="fake", model="deterministic-v1", input_per_million=Decimal(0),
    output_per_million=Decimal(0),
)  # fmt: skip


@pytest.fixture(autouse=True)
def clean_audit(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text("delete from public.audit_events where action = 'llm.run_settle'"))


def _now(engine: Engine) -> datetime:
    with engine.connect() as conn:
        return conn.execute(text("select now()")).scalar_one()


def _suggestion(
    engine: Engine, agent: uuid.UUID, status: str, *, price: ModelPrice = PRICE
) -> uuid.UUID:
    run = uuid.uuid4()
    with engine.begin() as conn:
        budget.record(conn, agent, None, price, "reserve", f"llm:{run}:reserve", call_reservation())
        conn.execute(
            text(
                "insert into public.llm_runs (id, agent_id, purpose, status, reserved_usd) "
                "values (:id, :a, 'suggestion', :s, :usd)"
            ),
            {"id": run, "a": agent, "s": status, "usd": call_reservation()},
        )
    return run


def _conversation(engine: Engine, agents: list[uuid.UUID], status: str) -> uuid.UUID:
    with engine.begin() as conn:
        conv = uuid.UUID(
            str(
                conn.execute(
                    text(
                        "insert into public.conversations (status, started_at) "
                        "values (:s, now()) returning id"
                    ),
                    {"s": status},
                ).scalar_one()
            )
        )
        for ordinal, agent in enumerate(agents, 1):
            conn.execute(
                text(
                    "insert into public.conversation_participants "
                    "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                    "values (:c, :a, :o, false, true)"
                ),
                {"c": conv, "a": agent, "o": ordinal},
            )
            budget.record(
                conn, agent, conv, PRICE, "reserve", f"session:{conv}:{agent}",
                session_reservation(),
            )  # fmt: skip
        conn.execute(
            text(
                "insert into public.llm_invocations (conversation_id, turn_no, status) "
                "values (:c, 1, 'started')"
            ),
            {"c": conv},
        )
    return conv


def _conversation_run(engine: Engine, agent: uuid.UUID, conv: uuid.UUID, status: str) -> uuid.UUID:
    run = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.llm_runs (id, agent_id, conversation_id, purpose, status, "
                "reserved_usd) values (:id, :a, :c, 'conversation', :s, :usd)"
            ),
            {"id": run, "a": agent, "c": conv, "s": status, "usd": call_reservation()},
        )
    return run


def _settle(engine: Engine, run: uuid.UUID, outcome: Any, **kw: Any) -> None:
    now = kw.pop("now", None) or _now(engine)
    with engine.begin() as conn:
        llm_reconcile.settle(conn, run, outcome, operator="sskohei", now=now, **kw)


def _one(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).first()


def _hold(engine: Engine, agent: uuid.UUID, conv: uuid.UUID | None = None) -> Decimal:
    with engine.connect() as conn:
        return Decimal(
            conn.execute(
                text(
                    "select coalesce(sum(case when kind = 'reserve' then estimated_usd "
                    "when kind = 'release' then -estimated_usd else 0 end), 0) "
                    "from public.usage_records where agent_id = :a and "
                    "conversation_id is not distinct from :c"
                ),
                {"a": agent, "c": conv},
            ).scalar_one()
        )


# -- 提案（会話に結び付かない呼び出し） ------------------------------------------------------


def test_charged_suggestion_records_charge_and_releases_rest(
    engine: Engine, ai_pair: list[uuid.UUID]
) -> None:
    agent = ai_pair[0]
    run = _suggestion(engine, agent, "unknown")
    _settle(engine, run, "charged", input_tokens=1000, output_tokens=200)
    charge = _one(
        engine,
        "select actual_usd, input_tokens, output_tokens from public.usage_records "
        "where idempotency_key = :k",
        k=f"llm:{run}:generation:charge",
    )
    assert (charge.actual_usd, charge.input_tokens, charge.output_tokens) == (
        PRICE.cost(1000, 200),
        1000,
        200,
    )
    assert _hold(engine, agent) == 0
    row = _one(engine, "select status, error_code from public.llm_runs where id = :id", id=run)
    assert tuple(row) == ("failed", "unknown_outcome")
    audit = _one(
        engine,
        "select resource_id, metadata from public.audit_events where action = 'llm.run_settle'",
    )
    assert audit.resource_id == str(run)
    assert audit.metadata == {
        "outcome": "charged",
        "phase": "generation",
        "input_tokens": 1000,
        "output_tokens": 200,
        "operator": "sskohei",
    }
    assert str(agent) not in json.dumps(audit.metadata)
    # 確定した実行はもう触れない
    with pytest.raises(llm_reconcile.RunNotFound):
        _settle(engine, run, "not_charged")


def test_not_charged_suggestion_only_releases(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    agent = ai_pair[0]
    run = _suggestion(engine, agent, "unknown")
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, run, "not_charged", input_tokens=10)
    assert exc.value.code == "invalid_tokens"
    _settle(engine, run, "not_charged")
    assert _hold(engine, agent) == 0
    assert _one(engine, "select count(*) as n from public.usage_records where agent_id = :a "
                "and kind = 'charge'", a=agent).n == 0  # fmt: skip


def test_recent_in_flight_run_is_not_settled(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    agent = ai_pair[0]
    run = _suggestion(engine, agent, "generating")
    now = _now(engine)
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, run, "not_charged", now=now)
    assert exc.value.code == "still_in_progress"
    assert run in {r.id for r in run_list(engine, now) if not r.stale}
    # 止まったまま 15 分を過ぎたら確定できる（監視でも知らせる）
    later = now + timedelta(minutes=16)
    with engine.connect() as conn:
        assert ops.snapshot(conn, later, max_daily_cost=Decimal("1"))["llm_stale"] >= 1
    _settle(engine, run, "charged", input_tokens=100, output_tokens=10, now=later)
    assert _one(engine, "select status from public.llm_runs where id = :id", id=run).status == (
        "failed"
    )


def run_list(engine: Engine, now: datetime) -> list[llm_reconcile.UnsettledRun]:
    with engine.connect() as conn:
        return llm_reconcile.list_unsettled(conn, now)


def test_phase_and_token_limits(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    agent = ai_pair[0]
    # 生成は課金済みで、安全判定の結果が分からない
    run = _suggestion(engine, agent, "unknown")
    with engine.begin() as conn:
        budget.record(conn, agent, None, PRICE, "charge", f"llm:{run}:generation:charge",
                      PRICE.cost(100, 10), input_tokens=100, output_tokens=10)  # fmt: skip
        budget.record(conn, agent, None, PRICE, "release", f"llm:{run}:generation:consume",
                      PRICE.cost(100, 10))  # fmt: skip
    assert [r.phase for r in run_list(engine, _now(engine)) if r.id == run] == ["moderation"]
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, run, "charged", input_tokens=1000, output_tokens=5)  # 安全判定の上限超え
    assert exc.value.code == "invalid_tokens"
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, run, "charged")
    assert exc.value.code == "invalid_tokens"
    _settle(engine, run, "charged", input_tokens=300, output_tokens=5)
    assert _one(engine, "select 1 as x from public.usage_records where idempotency_key = :k",
                k=f"llm:{run}:moderation:charge") is not None  # fmt: skip
    assert _hold(engine, agent) == 0


def test_nothing_in_flight_and_unknown_model(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    agent = ai_pair[0]
    reserved = _suggestion(engine, agent, "reserved")
    later = _now(engine) + timedelta(minutes=16)
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, reserved, "charged", input_tokens=10, output_tokens=1, now=later)
    assert exc.value.code == "nothing_in_flight"
    _settle(engine, reserved, "not_charged", now=later)

    fake = _suggestion(engine, agent, "unknown", price=FAKE)
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, fake, "charged", input_tokens=10, output_tokens=1)
    assert exc.value.code == "unknown_model"
    _settle(engine, fake, "not_charged")
    assert _hold(engine, agent) == 0
    # 解放は保留と同じモデル名で記録する（台帳の集計をずらさない）
    assert _one(engine, "select model from public.usage_records where idempotency_key = :k",
                k=f"llm:{fake}:end").model == "deterministic-v1"  # fmt: skip


# -- 会話 -----------------------------------------------------------------------------------


def test_conversation_run_releases_holds_after_all_runs_settle(
    engine: Engine, ai_pair: list[uuid.UUID]
) -> None:
    a, b = ai_pair
    conv = _conversation(engine, ai_pair, "failed")
    run_a = _conversation_run(engine, a, conv, "unknown")
    run_b = _conversation_run(engine, b, conv, "unknown")
    _settle(engine, run_a, "charged", input_tokens=2000, output_tokens=300)
    # もう一方が未確定の間は、どちらの保留も解放しない
    assert _hold(engine, a, conv) == session_reservation() - PRICE.cost(2000, 300)
    assert _hold(engine, b, conv) == session_reservation()
    _settle(engine, run_b, "not_charged")
    assert _hold(engine, a, conv) == 0
    assert _hold(engine, b, conv) == 0
    row = _one(engine, "select cost_usd from public.conversations where id = :c", c=conv)
    assert row.cost_usd == PRICE.cost(2000, 300)
    assert _one(engine, "select status from public.llm_invocations where conversation_id = :c",
                c=conv).status == "retryable"  # fmt: skip
    # 削除のガードも通る（アカウント削除のパージが待たなくてよい）
    with engine.begin() as conn:
        budget.require_settled(conn, conversation=conv)


def test_active_conversation_is_not_settled(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    a, _ = ai_pair
    conv = _conversation(engine, ai_pair, "running")
    run = _conversation_run(engine, a, conv, "unknown")
    with pytest.raises(llm_reconcile.CannotSettle) as exc:
        _settle(engine, run, "not_charged")
    assert exc.value.code == "conversation_active"
    assert _hold(engine, a, conv) == session_reservation()


# -- CLI ------------------------------------------------------------------------------------


def _deps(engine: Engine, stdin: str = "") -> tuple[cli.Deps, io.StringIO]:
    out = io.StringIO()
    settings = Settings(_env_file=None, database_url=str(engine.url))  # type: ignore[call-arg]
    return cli.Deps(settings, engine, io.StringIO(stdin), out, datetime.now(UTC)), out


def test_cli_lists_and_settles_without_agent_ids(engine: Engine, ai_pair: list[uuid.UUID]) -> None:
    agent = ai_pair[0]
    run = _suggestion(engine, agent, "unknown")
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", "--operator", "sskohei", "llm-list"], deps=deps) == 0
    assert str(run) in out.getvalue() and str(agent) not in out.getvalue()
    args = ["--operator", "sskohei", "llm-settle", str(run), "charged"]
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", *args], deps=deps) == cli.EXIT_FAILED
    assert "invalid_tokens" in out.getvalue()
    deps, out = _deps(engine)
    code = cli.main(
        ["--env", "local", *args, "--input-tokens", "10", "--output-tokens", "1"], deps=deps
    )
    assert code == cli.EXIT_OK
    deps, out = _deps(engine)
    assert cli.main(["--env", "local", *args, "--input-tokens", "1"], deps=deps) == 1
    assert "not_found" in out.getvalue()
