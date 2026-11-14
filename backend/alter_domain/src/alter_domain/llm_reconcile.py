"""LLM 呼び出しの結果不明の照合（#36、docs/27 §4）。

Worker・API は、送信した LLM 呼び出しの結果が分からないとき（タイムアウト・異常終了など）
再実行も保留の解放もしない（docs/08・09）。運営（sskohei）が Anthropic Console の使用量と
見比べたうえで、この関数で 1 件ずつ確定させる。

- ``charged``: 課金されていた。運営が確認したトークン数で課金を記録する（記録の鍵は
  通常の実行と同じ ``llm:{run}:{phase}`` なので、二重に記録しない）
- ``not_charged``: 課金されていなかった。課金は記録しない

どちらも、実行を ``failed``（``unknown_outcome``）で終え、残った保留を解放する。会話の
実行は、会話が終わっていて、ほかに未確定の実行がないときだけ、両参加者の会話の保留を
解放する（Worker の ``finish`` と同じ）。出力は使わないので、会話・提案には何も書かない。

まだ進行中かもしれない実行（``unknown`` 以外で、最後の更新から ``STALE_MINUTES`` 分以内）や、
実行中の会話の実行は確定させない。監査には操作の種類・実行 ID・結果・トークン数・運営者名
だけを残す（本文は残さない）。
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import Connection, text

from alter_domain import budget
from alter_domain.llm_contract import ModelPrice
from alter_domain.llm_selection import (
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    MODERATION_INPUT_TOKENS,
    MODERATION_OUTPUT_TOKENS,
    PROVIDER,
    model_price,
)

UNSETTLED = ("reserved", "generating", "generated", "moderating", "unknown")
# この時間を過ぎても状態が変わらない実行は、プロセスが止まったものとみなせる
# （1 回の呼び出しは Sonnet のタイムアウトと再試行を含めて数分で終わる）
STALE_MINUTES = 15
TERMINAL_CONVERSATION = ("completed", "failed", "cancelled")
# トークン数の上限（予約の計算に使う最大値。これを超える値は入力の誤りとみなす）
PHASE_LIMITS = {
    "generation": (MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS),
    "moderation": (MODERATION_INPUT_TOKENS, MODERATION_OUTPUT_TOKENS),
}


class RunNotFound(Exception):
    """指定した実行がない、または未確定ではない。"""


class CannotSettle(Exception):
    """今は確定できない（進行中・会話が実行中・入力の誤りなど）。``code`` は固定の文字列。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class UnsettledRun:
    id: uuid.UUID
    purpose: str
    status: str
    phase: str | None
    conversation_id: uuid.UUID | None
    conversation_status: str | None
    reserved_usd: str
    updated_at: datetime
    stale: bool


def list_unsettled(conn: Connection, now: datetime) -> list[UnsettledRun]:
    """未確定の実行の一覧（本文・利用者 ID・AI ID は出さない）。"""
    rows = conn.execute(
        text(
            "select r.id, r.purpose, r.status, r.conversation_id, c.status as conversation_status, "
            "r.reserved_usd, r.updated_at from public.llm_runs r "
            "left join public.conversations c on c.id = r.conversation_id "
            "where r.status = any(cast(:s as text[])) order by r.updated_at, r.id"
        ),
        {"s": list(UNSETTLED)},
    ).all()
    return [
        UnsettledRun(
            row.id,
            row.purpose,
            row.status,
            _phase(conn, row.id, row.status),
            row.conversation_id,
            row.conversation_status,
            str(row.reserved_usd),
            row.updated_at,
            _stale(row.status, row.updated_at, now),
        )
        for row in rows
    ]


def settle(
    conn: Connection,
    run_id: uuid.UUID,
    outcome: Literal["charged", "not_charged"],
    *,
    operator: str,
    now: datetime,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """結果不明の実行を確定させる（呼び出し側のトランザクションで）。"""
    # 費用台帳の書き込みは予算ロックで直列にする（Worker・API と同じ）
    budget.lock_budget(conn)
    run = conn.execute(
        text(
            "select r.id, r.agent_id, r.conversation_id, r.purpose, r.status, r.updated_at, "
            "c.status as conversation_status from public.llm_runs r "
            "left join public.conversations c on c.id = r.conversation_id "
            "where r.id = :id and r.status = any(cast(:s as text[])) for update of r"
        ),
        {"id": run_id, "s": list(UNSETTLED)},
    ).first()
    if run is None:
        raise RunNotFound()
    if not _stale(run.status, run.updated_at, now):
        raise CannotSettle("still_in_progress")
    conversation = run.conversation_id
    if run.purpose == "conversation" and run.conversation_status not in TERMINAL_CONVERSATION:
        # Worker がまだ会話を進めている（または再開する）間は触らない
        raise CannotSettle("conversation_active")

    phase = _phase(conn, run.id, run.status)
    ledger = _ledger(conn, run.agent_id, conversation, run.id)
    if outcome == "charged":
        if phase is None:
            # 送信中の呼び出しがない（送る前か、呼び出しの間で止まった）
            raise CannotSettle("nothing_in_flight")
        max_in, max_out = PHASE_LIMITS[phase]
        if not (0 <= input_tokens <= max_in and 0 <= output_tokens <= max_out) or (
            input_tokens == 0 and output_tokens == 0
        ):
            raise CannotSettle("invalid_tokens")
        if ledger is None or ledger.charge is None:
            raise CannotSettle("unknown_model")
        _charge(
            conn,
            run.agent_id,
            conversation,
            run.id,
            phase,
            ledger.charge,
            input_tokens,
            output_tokens,
        )
    elif input_tokens or output_tokens:
        raise CannotSettle("invalid_tokens")

    conn.execute(
        text(
            "update public.llm_runs set status = 'failed', error_code = 'unknown_outcome' "
            "where id = :id"
        ),
        {"id": run.id},
    )
    release = ledger.release if ledger is not None else None
    if conversation is None:
        _release_suggestion(conn, run.agent_id, run.id, release)
    else:
        _release_conversation(conn, conversation, release)
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (null, 'llm.run_settle', 'llm_run', :id, cast(:meta as jsonb))"
        ),
        {
            "id": str(run.id),
            "meta": json.dumps(
                {
                    "outcome": outcome,
                    "phase": phase,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "operator": operator[:40],
                }
            ),
        },
    )


# -- 内部 -----------------------------------------------------------------------------------


def _stale(status: str, updated_at: datetime, now: datetime) -> bool:
    return status == "unknown" or updated_at <= now - timedelta(minutes=STALE_MINUTES)


def _charged(conn: Connection, run: uuid.UUID, phase: str) -> bool:
    return (
        conn.execute(
            text("select 1 from public.usage_records where idempotency_key = :k"),
            {"k": f"llm:{run}:{phase}:charge"},
        ).first()
        is not None
    )


def _phase(conn: Connection, run: uuid.UUID, status: str) -> str | None:
    """結果が分からない（送信した可能性がある）呼び出しの段階。なければ None。"""
    if status == "generating":
        phase = "generation"
    elif status == "moderating":
        phase = "moderation"
    elif status == "unknown":
        phase = "moderation" if _charged(conn, run, "generation") else "generation"
    else:  # reserved / generated: 次の呼び出しを送る前
        return None
    return None if _charged(conn, run, phase) else phase


@dataclass(frozen=True)
class _Ledger:
    """保留を記録したときの供給元・モデル（台帳の集計をずらさないよう同じ名前で記録する）。"""

    release: ModelPrice  # 解放の記録用（単価は使わない）
    charge: ModelPrice | None  # 課金の記録用。単価を知っているモデル（ADR-008）だけ


def _ledger(
    conn: Connection, agent: uuid.UUID, conversation: uuid.UUID | None, run: uuid.UUID
) -> _Ledger | None:
    if conversation is None:
        row = conn.execute(
            text(
                "select provider, model from public.usage_records "
                "where idempotency_key = :k and kind = 'reserve'"
            ),
            {"k": f"llm:{run}:reserve"},
        ).first()
    else:
        row = conn.execute(
            text(
                "select provider, model from public.usage_records where agent_id = :a "
                "and conversation_id = :c and kind = 'reserve' order by created_at limit 1"
            ),
            {"a": agent, "c": conversation},
        ).first()
    if row is None:
        return None
    release = ModelPrice(
        provider=row.provider,
        model=row.model,
        input_per_million=Decimal(0),
        output_per_million=Decimal(0),
    )
    charge = model_price() if (row.provider, row.model) == (PROVIDER, MODEL) else None
    return _Ledger(release, charge)


def _charge(
    conn: Connection,
    agent: uuid.UUID,
    conversation: uuid.UUID | None,
    run: uuid.UUID,
    phase: str,
    price: ModelPrice,
    input_tokens: int,
    output_tokens: int,
) -> None:
    cost = price.cost(input_tokens, output_tokens)
    key = f"llm:{run}:{phase}"
    if conversation is not None:
        try:
            budget.consume(
                conn,
                agent,
                conversation,
                price,
                key,
                cost,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except budget.BudgetExceeded:
            raise CannotSettle("exceeds_hold") from None
        conn.execute(
            text(
                "update public.conversations set cost_usd = "
                "(select coalesce(sum(actual_usd), 0) from public.usage_records "
                "where conversation_id = :id and kind = 'charge') where id = :id"
            ),
            {"id": conversation},
        )
        return
    if cost > _suggestion_hold(conn, agent, run):
        raise CannotSettle("exceeds_hold")
    budget.record(
        conn,
        agent,
        None,
        price,
        "charge",
        key + ":charge",
        cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    budget.record(conn, agent, None, price, "release", key + ":consume", cost)


def _suggestion_hold(conn: Connection, agent: uuid.UUID, run: uuid.UUID) -> Decimal:
    return Decimal(
        conn.execute(
            text(
                "select coalesce(sum(case when kind = 'reserve' then estimated_usd "
                "when kind = 'release' then -estimated_usd else 0 end), 0) "
                "from public.usage_records where agent_id = :a "
                "and left(idempotency_key, length(:p)) = :p"
            ),
            {"a": agent, "p": f"llm:{run}:"},
        ).scalar_one()
    )


def _release_suggestion(
    conn: Connection, agent: uuid.UUID, run: uuid.UUID, price: ModelPrice | None
) -> None:
    remaining = _suggestion_hold(conn, agent, run)
    if remaining > 0 and price is not None:
        budget.record(conn, agent, None, price, "release", f"llm:{run}:end", remaining)


def _release_conversation(
    conn: Connection, conversation: uuid.UUID, price: ModelPrice | None
) -> None:
    # 送信中の印（llm_invocations の started）を外す。会話は終わっているので再開はしない
    conn.execute(
        text(
            "update public.llm_invocations set status = 'retryable' "
            "where conversation_id = :c and status in ('started', 'unknown')"
        ),
        {"c": conversation},
    )
    try:
        budget.require_settled(conn, conversation=conversation)
    except budget.UnsettledBudget:
        return  # もう一方の実行も確定させてから解放する
    if price is None:
        return
    participants = conn.execute(
        text(
            "select agent_id from public.conversation_participants "
            "where conversation_id = :c order by ordinal"
        ),
        {"c": conversation},
    ).scalars()
    for agent in participants:
        budget.release_session(conn, agent, conversation, price)
