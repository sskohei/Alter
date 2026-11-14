"""運用の観測と、配送の「要確認」の照合（#36、docs/27 運用手順）。

- ``snapshot``: 件数と経過時間だけを集める（本文・表示名・利用者 ID・Discord ID を含めない）
- ``alerts``: しきい値を超えたものを固定コードで返す。運営の定期実行・監視から呼ぶ
- 配送の照合: ``needs_review`` の行を、運営が Discord を確認したうえで ``sent`` か
  ``cancelled`` に確定させる。自動で送り直す操作は持たない（二重投稿を防ぐ）
- 投稿先の選択: 投稿先が複数で保留した会話（``ambiguous_target``）を、運営・管理者が
  1 つに決める。選ばれた行だけを ``pending`` に戻し、他は ``not_chosen`` で取り消す

監査には操作の種類・対象の配信行 ID・結果・運営者名だけを残す（本文は残さない）。
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Connection, text

from alter_domain import budget, llm_reconcile
from alter_domain.discord_delivery import _postable_channels

# しきい値（運用手順 docs/27 と合わせる）
JOB_WAIT_ALERT_MINUTES = 30
LEASE_GRACE_MINUTES = 5
BUDGET_ALERT_RATIO = Decimal("0.8")
DELETION_DEADLINE_DAYS = 30
DELETION_ALERT_DAYS = 25
UNSETTLED_LLM = ("reserved", "generating", "generated", "moderating", "unknown")


class ReviewNotFound(Exception):
    """指定した行がない、または要確認（needs_review）ではない。"""


class InvalidResolution(Exception):
    """照合の入力が足りない・形式が違う。"""


# -- 観測 -----------------------------------------------------------------------------------


def snapshot(conn: Connection, now: datetime, *, max_daily_cost: Decimal) -> dict[str, Any]:
    """監視用の件数をまとめる。返す値は数値・日時・固定の文字列だけ。"""
    jobs = {}
    for row in conn.execute(
        text(
            "select kind, "
            "count(*) filter (where status in ('queued','retry_wait') and run_at <= :now) "
            "  as due, "
            "min(run_at) filter (where status in ('queued','retry_wait') and run_at <= :now) "
            "  as oldest_due, "
            "count(*) filter (where status = 'running' and lease_until < :stale) "
            "  as lease_expired, "
            "count(*) filter (where status = 'failed' and updated_at >= :day) as failed_24h, "
            "count(*) filter (where status = 'completed' and updated_at >= :day) "
            "  as completed_24h "
            "from public.background_jobs group by kind order by kind"
        ),
        {
            "now": now,
            "stale": now - timedelta(minutes=LEASE_GRACE_MINUTES),
            "day": now - timedelta(days=1),
        },
    ):
        jobs[row.kind] = {
            "due": row.due,
            "oldest_due_minutes": _minutes(now, row.oldest_due),
            "lease_expired": row.lease_expired,
            "failed_24h": row.failed_24h,
            "completed_24h": row.completed_24h,
        }

    llm = dict.fromkeys(UNSETTLED_LLM, 0)
    for row in conn.execute(
        text(
            "select status, count(*) as n from public.llm_runs "
            "where status = any(cast(:s as text[])) group by status"
        ),
        {"s": list(UNSETTLED_LLM)},
    ):
        llm[row.status] = row.n
    # unknown 以外で状態が止まった実行（プロセスの異常終了など。docs/27 §4 で照合する）
    llm_stale = conn.execute(
        text(
            "select count(*) from public.llm_runs where status = any(cast(:s as text[])) "
            "and updated_at <= :stale"
        ),
        {
            "s": [s for s in UNSETTLED_LLM if s != "unknown"],
            "stale": now - timedelta(minutes=llm_reconcile.STALE_MINUTES),
        },
    ).scalar_one()
    invocations = {
        str(status): int(n)
        for status, n in conn.execute(
            text(
                "select status, count(*) from public.llm_invocations "
                "where status in ('started','unknown') group by status"
            )
        )
    }

    # 費用の上限は UTC の日付で数える（budget.exposure と同じ）
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    usage = conn.execute(
        text(
            "select coalesce(sum(actual_usd), 0) as charged, "
            "coalesce(sum(input_tokens), 0) as input_tokens, "
            "coalesce(sum(output_tokens), 0) as output_tokens "
            "from public.usage_records where kind = 'charge' and created_at >= :d"
        ),
        {"d": day_start},
    ).one()
    exposure = budget.exposure(conn, now=now)

    def outbox(table: str) -> dict[str, int]:
        counts = dict.fromkeys(("pending", "sending", "needs_review"), 0)
        for status, n in conn.execute(
            text(
                f"select status, count(*) from public.{table} "  # noqa: S608
                "where status in ('pending','sending','needs_review') group by status"
            )
        ):
            counts[status] = n
        return counts

    cleanup = {
        str(status): int(n)
        for status, n in conn.execute(
            text("select status, count(*) from public.discord_cleanup_tasks group by status")
        )
    }
    deletion = conn.execute(
        text(
            "select count(*) filter (where status in ('pending','running')) as open, "
            "count(*) filter (where status = 'failed') as failed, "
            "min(requested_at) filter (where status in ('pending','running')) as oldest_open "
            "from public.deletion_requests"
        )
    ).one()

    return {
        "at": now.isoformat(),
        "jobs": jobs,
        "llm_unsettled": llm,
        "llm_stale": int(llm_stale),
        "llm_invocations": invocations,
        "cost": {
            "charged_today_usd": str(usage.charged),
            "exposure_usd": str(exposure),
            "daily_limit_usd": str(max_daily_cost),
            "input_tokens_today": int(usage.input_tokens),
            "output_tokens_today": int(usage.output_tokens),
        },
        "discord": {
            "posts": outbox("discord_deliveries"),
            "dms": outbox("discord_dm_deliveries"),
            "thread_cleanup": cleanup,
        },
        "deletion": {
            "open": deletion.open,
            "failed": deletion.failed,
            "oldest_open_days": _days(now, deletion.oldest_open),
        },
    }


def alerts(snap: dict[str, Any]) -> list[str]:
    """しきい値を超えたものを固定コードで返す（運営が手順 docs/27 で対応する）。"""
    found: list[str] = []
    for kind, j in snap["jobs"].items():
        if (j["oldest_due_minutes"] or 0) >= JOB_WAIT_ALERT_MINUTES:
            found.append(f"jobs_waiting:{kind}")
        if j["lease_expired"]:
            found.append(f"lease_expired:{kind}")
        if j["failed_24h"]:
            found.append(f"jobs_failed:{kind}")
    if snap["llm_unsettled"]["unknown"] or snap["llm_invocations"].get("unknown"):
        found.append("llm_unknown_outcome")
    if snap["llm_stale"]:
        found.append("llm_stale_in_flight")
    cost = snap["cost"]
    limit = Decimal(cost["daily_limit_usd"])
    if limit > 0 and Decimal(cost["exposure_usd"]) >= limit * BUDGET_ALERT_RATIO:
        found.append("budget_near_limit")
    if snap["discord"]["posts"]["needs_review"] or snap["discord"]["dms"]["needs_review"]:
        found.append("discord_needs_review")
    if snap["discord"]["thread_cleanup"].get("failed"):
        found.append("discord_cleanup_failed")
    deletion = snap["deletion"]
    if deletion["failed"]:
        found.append("deletion_failed")
    if (deletion["oldest_open_days"] or 0) >= DELETION_ALERT_DAYS:
        found.append("deletion_near_deadline")
    return found


def _minutes(now: datetime, at: datetime | None) -> int | None:
    return None if at is None else max(0, int((now - at).total_seconds() // 60))


def _days(now: datetime, at: datetime | None) -> int | None:
    return None if at is None else max(0, (now - at).days)


# -- 配送の照合 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    kind: Literal["post", "dm"]
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    channel_id: str | None
    thread_id: str | None
    last_error: str | None
    attempts: int
    updated_at: datetime


def list_review(conn: Connection) -> list[ReviewItem]:
    """要確認の配信行（本文・Discord ユーザー ID は出さない）。"""
    items = [
        ReviewItem(
            "post",
            row.id,
            row.conversation_id,
            row.channel_id,
            row.discord_thread_id,
            row.last_error,
            row.attempts,
            row.updated_at,
        )
        for row in conn.execute(
            text(
                "select id, conversation_id, channel_id, discord_thread_id, last_error, attempts, "
                "updated_at from public.discord_deliveries where status = 'needs_review' "
                "order by updated_at, id"
            )
        )
    ]
    items += [
        ReviewItem("dm", row.id, None, None, None, row.last_error, row.attempts, row.updated_at)
        for row in conn.execute(
            text(
                "select id, last_error, attempts, updated_at from public.discord_dm_deliveries "
                "where status = 'needs_review' order by updated_at, id"
            )
        )
    ]
    return items


def resolve_post(
    conn: Connection,
    delivery_id: uuid.UUID,
    outcome: Literal["sent", "cancelled"],
    *,
    operator: str,
    thread_id: str | None = None,
    message_id: str | None = None,
) -> None:
    """Discord で確認した結果を記録する。``sent`` は実在するスレッドと最初の発言の ID が必要。"""
    if outcome == "sent":
        if not (_snowflake(thread_id) and _snowflake(message_id)):
            raise InvalidResolution("thread_id and message_id are required for sent")
        params: dict[str, Any] = {"t": thread_id, "m": message_id}
        sets = "status = 'sent', discord_thread_id = :t, discord_message_id = :m, last_error = null"
    else:
        params = {}
        sets = "status = 'cancelled', last_error = 'resolved_cancelled'"
    row = conn.execute(
        text(
            f"update public.discord_deliveries set {sets} "  # noqa: S608
            "where id = :id and status = 'needs_review' returning id"
        ),
        {"id": delivery_id, **params},
    ).first()
    if row is None:
        raise ReviewNotFound()
    _audit(conn, "discord.delivery_resolve", "discord_delivery", delivery_id, outcome, operator)


def resolve_dm(
    conn: Connection,
    delivery_id: uuid.UUID,
    outcome: Literal["sent", "cancelled"],
    *,
    operator: str,
    message_id: str | None = None,
) -> None:
    if outcome == "sent":
        if not _snowflake(message_id):
            raise InvalidResolution("message_id is required for sent")
        sets = "status = 'sent', discord_message_id = :m, last_error = null"
        params: dict[str, Any] = {"m": message_id}
    else:
        sets = "status = 'cancelled', last_error = 'resolved_cancelled'"
        params = {}
    row = conn.execute(
        text(
            f"update public.discord_dm_deliveries set {sets} "  # noqa: S608
            "where id = :id and status = 'needs_review' returning id"
        ),
        {"id": delivery_id, **params},
    ).first()
    if row is None:
        raise ReviewNotFound()
    _audit(conn, "discord.dm_resolve", "discord_dm_delivery", delivery_id, outcome, operator)


def choose_target(
    conn: Connection, conversation_id: uuid.UUID, channel_id: str, *, operator: str
) -> None:
    """投稿先が複数で保留した会話の投稿先を 1 つに決める。

    選んだ投稿先が今も条件を満たすことを確かめてから、その行だけを ``pending`` に戻し、
    他の保留行を ``cancelled``（``not_chosen``）にする。取り出し時は ``not_chosen`` の
    投稿先を候補から外すので、再び保留にはならない。
    """
    rows = conn.execute(
        text(
            "select id, channel_id from public.discord_deliveries "
            "where conversation_id = :c and status = 'needs_review' "
            "and last_error = 'ambiguous_target' order by channel_id for update"
        ),
        {"c": conversation_id},
    ).all()
    chosen = next((r for r in rows if r.channel_id == channel_id), None)
    if chosen is None:
        raise ReviewNotFound()
    if channel_id not in _postable_channels(conn, conversation_id):
        raise InvalidResolution("the chosen channel is no longer eligible")
    conn.execute(
        text(
            "update public.discord_deliveries set status = 'cancelled', last_error = 'not_chosen' "
            "where conversation_id = :c and status = 'needs_review' "
            "and last_error = 'ambiguous_target' and id <> :id"
        ),
        {"c": conversation_id, "id": chosen.id},
    )
    conn.execute(
        text(
            "update public.discord_deliveries set status = 'pending', last_error = null, "
            "next_attempt_at = now() where id = :id"
        ),
        {"id": chosen.id},
    )
    _audit(conn, "discord.target_choose", "discord_delivery", chosen.id, "chosen", operator)


def _snowflake(value: str | None) -> bool:
    return value is not None and value.isdigit() and 15 <= len(value) <= 21


def _audit(
    conn: Connection,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    outcome: str,
    operator: str,
) -> None:
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (null, :action, :type, :id, cast(:meta as jsonb))"
        ),
        {
            "action": action,
            "type": resource_type,
            "id": str(resource_id),
            "meta": json.dumps({"outcome": outcome, "operator": operator[:40]}),
        },
    )
