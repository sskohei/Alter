"""Append-only USD ledger. All writers take one advisory transaction lock before agent locks.

Outstanding holds include previous UTC days; unknown outcomes retain the hold until reconciliation.
Amounts must be worst-case bounds (including provider retries), not expected averages.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, text

from alter_domain.llm_contract import ModelPrice

BUDGET_LOCK = 660018


class BudgetExceeded(Exception):
    pass


class LedgerConflict(Exception):
    pass


def lock_budget(conn: Connection) -> None:
    conn.execute(text("select pg_advisory_xact_lock(:key)"), {"key": BUDGET_LOCK})


def amount(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.000001")):
        raise ValueError("USD must be nonnegative with at most six decimal places")
    return value


def exposure(
    conn: Connection, *, agent: UUID | None = None, now: datetime | None = None
) -> Decimal:
    return Decimal(
        conn.execute(
            text(
                "select coalesce(sum(case when kind='reserve' then estimated_usd "
                "when kind='release' then -estimated_usd "
                "when kind='charge' and created_at >= date_trunc('day', "
                "coalesce(cast(:now as timestamptz),now()) at time zone 'UTC') at time zone 'UTC' "
                "then actual_usd else 0 end),0) from public.usage_records "
                "where (cast(:agent as uuid) is null or agent_id=:agent)"
            ),
            {"agent": agent, "now": now},
        ).scalar_one()
    )


def record(
    conn: Connection,
    agent: UUID,
    conversation: UUID | None,
    price: ModelPrice,
    kind: str,
    key: str,
    usd: Decimal,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    amount(usd)
    old = conn.execute(
        text(
            "select "
            "agent_id,conversation_id,kind,estimated_usd,provider,model,input_tokens,output_tokens "
            "from public.usage_records "
            "where idempotency_key=:key"
        ),
        {"key": key},
    ).first()
    if old is not None:
        if (
            old.agent_id,
            old.conversation_id,
            old.kind,
            old.estimated_usd,
            old.provider,
            old.model,
            old.input_tokens,
            old.output_tokens,
        ) != (
            agent,
            conversation,
            kind,
            usd,
            price.provider,
            price.model,
            input_tokens,
            output_tokens,
        ):
            raise LedgerConflict()
        return
    conn.execute(
        text(
            "insert into public.usage_records "
            "(agent_id,conversation_id,provider,model,input_tokens,output_tokens,estimated_usd,"
            "actual_usd,kind,idempotency_key) "
            "values (:agent,:conversation,:provider,:model,:i,:o,:usd,:actual,:kind,:key)"
        ),
        {
            "agent": agent,
            "conversation": conversation,
            "provider": price.provider,
            "model": price.model,
            "i": input_tokens,
            "o": output_tokens,
            "usd": usd,
            "actual": usd if kind == "charge" else None,
            "kind": kind,
            "key": key,
        },
    )


def reserve(
    conn: Connection,
    agent: UUID,
    conversation: UUID | None,
    price: ModelPrice,
    key: str,
    usd: Decimal,
    global_limit: Decimal,
    *,
    now: datetime | None = None,
) -> None:
    lock_budget(conn)
    amount(usd)
    amount(global_limit)
    if conn.execute(
        text("select 1 from public.usage_records where idempotency_key=:key"), {"key": key}
    ).first():
        record(conn, agent, conversation, price, "reserve", key, usd)
        return
    settings = conn.execute(
        text(
            "select daily_budget_usd,max_session_usd from public.agent_settings "
            "where agent_id=:id for update"
        ),
        {"id": agent},
    ).one()
    if (
        usd > settings.max_session_usd
        or exposure(conn, agent=agent, now=now) + usd > settings.daily_budget_usd
        or exposure(conn, now=now) + usd > global_limit
    ):
        raise BudgetExceeded()
    record(conn, agent, conversation, price, "reserve", key, usd)


def held(conn: Connection, agent: UUID, conversation: UUID) -> Decimal:
    return Decimal(
        conn.execute(
            text(
                "select coalesce(sum(case when kind='reserve' then estimated_usd "
                "when kind='release' then -estimated_usd else 0 end),0) from public.usage_records "
                "where agent_id=:agent and conversation_id=:conv"
            ),
            {"agent": agent, "conv": conversation},
        ).scalar_one()
    )


def consume(
    conn: Connection,
    agent: UUID,
    conversation: UUID,
    price: ModelPrice,
    key: str,
    usd: Decimal,
    *,
    input_tokens: int,
    output_tokens: int,
) -> None:
    lock_budget(conn)
    amount(usd)
    if conn.execute(
        text("select 1 from public.usage_records where idempotency_key=:key"),
        {"key": key + ":charge"},
    ).first():
        record(
            conn,
            agent,
            conversation,
            price,
            "charge",
            key + ":charge",
            usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return
    if usd > held(conn, agent, conversation):
        raise BudgetExceeded()
    record(
        conn,
        agent,
        conversation,
        price,
        "charge",
        key + ":charge",
        usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    record(conn, agent, conversation, price, "release", key + ":consume", usd)


def release_session(conn: Connection, agent: UUID, conversation: UUID, price: ModelPrice) -> None:
    lock_budget(conn)
    remaining = held(conn, agent, conversation)
    if remaining > 0:
        record(
            conn, agent, conversation, price, "release", f"{conversation}:{agent}:end", remaining
        )
