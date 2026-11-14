"""交流設定・ブロック・通知設定の共通ドメイン処理（#16、docs/09・docs/15・docs/23）。

各関数は API の共通認可（招待・停止/削除の確認）を通ったトランザクションの中で呼ぶ。
自律交流の OFF とブロックは、まだ始まっていない交流（``conversations.status='queued'``）と
そのジョブを取り消す。実行中の交流は Worker が各発言の直前に設定とブロックを再確認して止める
（docs/09）。予約した費用の解放は、取り消した会話の終了処理（#19/#20）で行う。
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, Row, text

USD_STEP = Decimal("0.000001")
# 入力として受け付ける金額の上限。運営上限よりも十分大きく、桁あふれ（500）を防ぐ
USD_INPUT_MAX = Decimal("1000000")
_SETTINGS_COLUMNS = (
    "autonomy_enabled, frequency_per_day, daily_budget_usd, max_session_usd, "
    "web_visibility, discord_publish, updated_at"
)
_FIELDS = (
    "autonomy_enabled",
    "frequency_per_day",
    "daily_budget_usd",
    "max_session_usd",
    "web_visibility",
    "discord_publish",
)


class AgentNotFound(Exception):
    """本人の AI ではない・存在しない・削除済み（API は 404 で存在を隠す）。"""


class SettingsConflict(Exception):
    """今の状態では変更できない（API は 409）。``reason`` は固定の短い理由。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SettingsInvalid(ValueError):
    """保存済みの値と組み合わせると不正な入力（API は 422）。"""


@dataclass(frozen=True)
class AgentSettingsRecord:
    autonomy_enabled: bool
    frequency_per_day: int
    daily_budget_usd: Decimal
    max_session_usd: Decimal
    web_visibility: str
    discord_publish: bool
    updated_at: datetime


@dataclass(frozen=True)
class PreferencesRecord:
    web_notifications: bool
    discord_dm_notifications: bool


def usd(value: Decimal) -> Decimal:
    """0 以上・小数 6 桁までの USD。それ以外は SettingsInvalid（巨大な値も 500 にしない）。"""
    message = "USD amounts must be >= 0 with at most 6 decimal places"
    if not value.is_finite() or value < 0 or value > USD_INPUT_MAX:
        raise SettingsInvalid(message)
    try:
        quantized = value.quantize(USD_STEP)
    except InvalidOperation:
        raise SettingsInvalid(message) from None
    if value != quantized:
        raise SettingsInvalid(message)
    return value


def _settings(row: Row[Any]) -> AgentSettingsRecord:
    return AgentSettingsRecord(
        autonomy_enabled=row.autonomy_enabled,
        frequency_per_day=row.frequency_per_day,
        daily_budget_usd=row.daily_budget_usd,
        max_session_usd=row.max_session_usd,
        web_visibility=row.web_visibility,
        discord_publish=row.discord_publish,
        updated_at=row.updated_at,
    )


def _owned_agent(
    conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, *, lock: bool
) -> Row[Any]:
    row = conn.execute(
        text(
            "select id, status, approved_profile_version from public.agents "
            "where id = :id and owner_id = :owner and status <> 'deleted'"
            + (" for update" if lock else "")
        ),
        {"id": agent_id, "owner": owner},
    ).first()
    if row is None:
        raise AgentNotFound()
    return row


def get_settings(conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID) -> AgentSettingsRecord:
    _owned_agent(conn, owner, agent_id, lock=False)
    row = conn.execute(
        text(f"select {_SETTINGS_COLUMNS} from public.agent_settings where agent_id = :id"),
        {"id": agent_id},
    ).one()
    return _settings(row)


def _audit(
    conn: Connection,
    owner: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any],
) -> None:
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (:owner, :action, :type, :id, cast(:metadata as jsonb))"
        ),
        {
            "owner": owner,
            "action": action,
            "type": resource_type,
            "id": resource_id,
            "metadata": json.dumps(metadata, default=str),
        },
    )


def cancel_queued_interactions(
    conn: Connection, agent_id: uuid.UUID, other_agent_id: uuid.UUID | None = None
) -> int:
    """まだ始まっていない交流と、そのジョブを取り消す。取り消した会話の数を返す。

    ``other_agent_id`` を指定すると、その相手との交流だけを対象にする（ブロック）。
    会話が ``queued`` でも、ジョブを Worker が取得済み（``running``）のものは取り消さない。
    実行中の交流は Worker が発言前の再確認で止め、予約を精算する（#20）。
    取り消した会話の未使用予約の解放は、#59 の共通解放処理を使う別 PR で接続する。
    """
    candidates = conn.execute(
        text(
            "select c.id, c.job_id from public.conversations c "
            "join public.conversation_participants p "
            "  on p.conversation_id = c.id and p.agent_id = :agent "
            "where c.status = 'queued' "
            "  and (cast(:other as uuid) is null or exists ("
            "    select 1 from public.conversation_participants o "
            "    where o.conversation_id = c.id and o.agent_id = cast(:other as uuid))) "
            "order by c.id for update of c"
        ),
        {"agent": agent_id, "other": other_agent_id},
    ).all()
    if not candidates:
        return 0
    job_ids = [row.job_id for row in candidates if row.job_id is not None]
    # ジョブも行ロックしてから状態を見る（Worker の取得と競合しても判断がぶれない）
    job_status = {
        row.id: row.status
        for row in conn.execute(
            text(
                "select id, status from public.background_jobs "
                "where id = any(:ids) order by id for update"
            ),
            {"ids": job_ids},
        )
    }
    targets = [
        row
        for row in candidates
        if row.job_id is None or job_status.get(row.job_id) in ("queued", "retry_wait")
    ]
    if not targets:
        return 0
    conversation_ids = [row.id for row in targets]
    target_jobs = [row.job_id for row in targets if row.job_id is not None]
    conn.execute(
        text(
            "update public.conversations set status = 'cancelled', ended_at = now() "
            "where id = any(:ids) and status = 'queued'"
        ),
        {"ids": conversation_ids},
    )
    if target_jobs:
        conn.execute(
            text(
                "update public.background_jobs set status = 'cancelled' "
                "where id = any(:ids) and status in ('queued', 'retry_wait')"
            ),
            {"ids": target_jobs},
        )
    return len(conversation_ids)


def cancel_pending_posts(
    conn: Connection, agent_id: uuid.UUID, other_agent_id: uuid.UUID | None = None
) -> int:
    """Cancel only unsent guild posts; keep sending/sent posts and independent consents."""
    rows = conn.execute(
        text(
            "update public.discord_deliveries d set status='cancelled' "
            "where d.status='pending' and exists ("
            "select 1 from public.conversation_participants p "
            "where p.conversation_id=d.conversation_id and p.agent_id=:agent) "
            "and (cast(:other as uuid) is null or exists ("
            "select 1 from public.conversation_participants o "
            "where o.conversation_id=d.conversation_id and o.agent_id=cast(:other as uuid))) "
            "returning d.id"
        ),
        {"agent": agent_id, "other": other_agent_id},
    ).all()
    return len(rows)


def update_settings(
    conn: Connection,
    owner: uuid.UUID,
    agent_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    max_daily_budget_usd: Decimal,
    consent_version: str,
) -> AgentSettingsRecord:
    """交流設定を部分更新する。変更がなければ監査も書かずに現在値を返す。"""
    agent = _owned_agent(conn, owner, agent_id, lock=True)
    current = _settings(
        conn.execute(
            text(
                f"select {_SETTINGS_COLUMNS} from public.agent_settings "
                "where agent_id = :id for update"
            ),
            {"id": agent_id},
        ).one()
    )
    merged = {field: getattr(current, field) for field in _FIELDS}
    for field, value in changes.items():
        merged[field] = usd(value) if field.endswith("_usd") else value

    changed = sorted(f for f in _FIELDS if merged[f] != getattr(current, f))
    if not changed:
        return current

    turning_on = [
        f for f in ("autonomy_enabled", "discord_publish") if merged[f] and not getattr(current, f)
    ]
    # 予算の上限は、予算を変えるときと同意を ON にするときだけ確認する。
    # 運営が上限を下げたあとでも、OFF（同意の撤回）や頻度の変更は既存予算の修正なしに通す。
    if any(f.endswith("_usd") for f in changes) or turning_on:
        if merged["daily_budget_usd"] > max_daily_budget_usd:
            raise SettingsInvalid("daily_budget_usd exceeds the operator limit")
        if merged["max_session_usd"] > merged["daily_budget_usd"]:
            raise SettingsInvalid("max_session_usd must not exceed daily_budget_usd")
    if turning_on:
        # 承認済みで稼働中の AI だけが交流・公開に同意できる（停止中・下書きは不可）
        if agent.status != "active" or agent.approved_profile_version is None:
            raise SettingsConflict("agent_not_active")
        if "autonomy_enabled" in turning_on and not consent_version:
            raise SettingsConflict("consent_terms_unavailable")

    row = conn.execute(
        text(
            "update public.agent_settings set "
            "autonomy_enabled = :autonomy_enabled, frequency_per_day = :frequency_per_day, "
            "daily_budget_usd = :daily_budget_usd, max_session_usd = :max_session_usd, "
            "web_visibility = :web_visibility, discord_publish = :discord_publish "
            f"where agent_id = :agent_id returning {_SETTINGS_COLUMNS}"
        ),
        {**merged, "agent_id": agent_id},
    ).one()

    cancelled = 0
    if current.autonomy_enabled and not merged["autonomy_enabled"]:
        cancelled = cancel_queued_interactions(conn, agent_id)
    posts_cancelled = 0
    if (current.autonomy_enabled and not merged["autonomy_enabled"]) or (
        current.discord_publish and not merged["discord_publish"]
    ):
        posts_cancelled = cancel_pending_posts(conn, agent_id)

    metadata: dict[str, Any] = {"changed": changed, "result": "success"}
    for field in ("autonomy_enabled", "discord_publish"):
        if field in changed:
            metadata[field] = merged[field]
    if "autonomy_enabled" in turning_on:
        metadata["consent_version"] = consent_version
    if cancelled:
        metadata["cancelled_conversations"] = cancelled
    if posts_cancelled:
        metadata["cancelled_posts"] = posts_cancelled
    _audit(conn, owner, "agent_settings.update", "agent", str(agent_id), metadata)
    return _settings(row)


def _lock_pair(
    conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, other_agent_id: uuid.UUID
) -> bool:
    """自分と相手の AI を ID の昇順でロックする。相手が存在すれば True。

    A→B と B→A のブロックが同時に来ても、両方が同じ順でロックするのでデッドロックしない
    （ブロック行の外部キー検査が相手の AI 行を参照するため、自分だけ先にロックすると交差する）。
    """
    rows = conn.execute(
        text(
            "select id, owner_id, status from public.agents "
            "where id in (:me, :other) order by id for update"
        ),
        {"me": agent_id, "other": other_agent_id},
    ).all()
    by_id = {row.id: row for row in rows}
    mine = by_id.get(agent_id)
    if mine is None or mine.owner_id != owner or mine.status == "deleted":
        raise AgentNotFound()
    other = by_id.get(other_agent_id)
    return other is not None and other.status != "deleted"


def block_agent(
    conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, other_agent_id: uuid.UUID
) -> bool:
    """相手をブロックする。新しくブロックしたら True。再送は何もしない（冪等）。"""
    if agent_id == other_agent_id:
        raise SettingsInvalid("an agent cannot block itself")
    if not _lock_pair(conn, owner, agent_id, other_agent_id):
        raise AgentNotFound()
    inserted = conn.execute(
        text(
            "insert into public.agent_blocks (blocker_agent_id, blocked_agent_id) "
            "values (:me, :other) on conflict do nothing returning blocker_agent_id"
        ),
        {"me": agent_id, "other": other_agent_id},
    ).first()
    if inserted is None:
        return False
    cancelled = cancel_queued_interactions(conn, agent_id, other_agent_id)
    posts_cancelled = cancel_pending_posts(conn, agent_id, other_agent_id)
    metadata: dict[str, Any] = {"result": "success"}
    if cancelled:
        metadata["cancelled_conversations"] = cancelled
    if posts_cancelled:
        metadata["cancelled_posts"] = posts_cancelled
    # 相手の AI の ID は監査に入れない（ブロックした相手を運営記録から辿らせない）
    _audit(conn, owner, "agent.block", "agent", str(agent_id), metadata)
    return True


def unblock_agent(
    conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, other_agent_id: uuid.UUID
) -> bool:
    """ブロックを解除する。解除したら True。ブロックしていなければ何もしない（冪等）。"""
    if agent_id == other_agent_id:
        raise SettingsInvalid("an agent cannot block itself")
    _lock_pair(conn, owner, agent_id, other_agent_id)
    deleted = conn.execute(
        text(
            "delete from public.agent_blocks "
            "where blocker_agent_id = :me and blocked_agent_id = :other "
            "returning blocker_agent_id"
        ),
        {"me": agent_id, "other": other_agent_id},
    ).first()
    if deleted is None:
        return False
    _audit(conn, owner, "agent.unblock", "agent", str(agent_id), {"result": "success"})
    return True


def get_preferences(conn: Connection, owner: uuid.UUID) -> PreferencesRecord:
    row = conn.execute(
        text(
            "select web_notifications, discord_dm_notifications from public.profiles "
            "where id = :id and deleted_at is null"
        ),
        {"id": owner},
    ).one()
    return PreferencesRecord(row.web_notifications, row.discord_dm_notifications)


def update_preferences(
    conn: Connection, owner: uuid.UUID, changes: dict[str, bool]
) -> PreferencesRecord:
    current = get_preferences(conn, owner)
    merged = {
        "web_notifications": changes.get("web_notifications", current.web_notifications),
        "discord_dm_notifications": changes.get(
            "discord_dm_notifications", current.discord_dm_notifications
        ),
    }
    changed = sorted(k for k, v in merged.items() if v != getattr(current, k))
    if not changed:
        return current
    row = conn.execute(
        text(
            "update public.profiles set web_notifications = :web_notifications, "
            "discord_dm_notifications = :discord_dm_notifications "
            "where id = :id and deleted_at is null "
            "returning web_notifications, discord_dm_notifications"
        ),
        {**merged, "id": owner},
    ).one()
    metadata: dict[str, Any] = {"changed": changed, "result": "success"}
    metadata.update({k: merged[k] for k in changed})
    _audit(conn, owner, "preferences.update", "profile", str(owner), metadata)
    return PreferencesRecord(row.web_notifications, row.discord_dm_notifications)
