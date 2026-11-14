"""アカウント削除要求の受付と即時停止（#30 前半、docs/06・docs/23）。

受付（``request_deletion``）は 1 つの DB トランザクションで次を確定する。途中で失敗したら全部戻す。
1. ``profiles.deleted_at`` を記録する（以後の業務 API は 403 ``ACCOUNT_INACTIVE``、
   共有会話・通知は相手の画面からも外れる）
2. 本人の AI の自律交流を OFF にし、稼働中なら ``paused`` にする
3. 本人の AI が参加する未開始（queued）の交流とジョブを取り消す
4. 未配信の取消（``invitations.cancel_pending_deliveries``。未送信の DM・投稿とギルド別同意）
5. 招待を失効する（招待の識別情報は 30 日以内にパージ。#30 後半）
6. ``deletion_requests`` を ``pending`` で作り、非同期パージのジョブ ``account.purge`` を積む
7. 監査に要求 ID だけを残す（表示名などの個人情報は残さない）

実際のデータ削除（会話→記憶→通知→AI→プロフィール→Auth）は #30 後半のジョブが行う。
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text

from alter_domain.interaction_settings import cancel_queued_interactions
from alter_domain.invitations import cancel_pending_deliveries, stop_agents_for_owner

PURGE_JOB_KIND = "account.purge"
# 再認証として認める方法と有効期間（Web の再認証画面と同じ 5 分。時計のずれを 30 秒許す）
REAUTH_METHODS = frozenset({"oauth"})
REAUTH_MAX_AGE_SECONDS = 300
REAUTH_CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class DeletionRecord:
    id: uuid.UUID
    status: str
    requested_at: datetime
    completed_at: datetime | None


def recently_reauthenticated(auth_methods: tuple[tuple[str, int], ...], now: float) -> bool:
    """JWT の ``amr`` に、5 分以内の OAuth ログインが含まれるか。

    トークンの更新（refresh）では ``amr`` の時刻は変わらないので、古いログインの使い回しは通らない。
    """
    return any(
        method in REAUTH_METHODS
        and now - REAUTH_MAX_AGE_SECONDS <= timestamp <= now + REAUTH_CLOCK_SKEW_SECONDS
        for method, timestamp in auth_methods
    )


def latest_request(conn: Connection, owner: uuid.UUID) -> DeletionRecord | None:
    row = conn.execute(
        text(
            "select id, status, requested_at, completed_at from public.deletion_requests "
            "where profile_id = :owner order by requested_at desc, id desc limit 1"
        ),
        {"owner": owner},
    ).first()
    if row is None:
        return None
    return DeletionRecord(row.id, row.status, row.requested_at, row.completed_at)


def request_deletion(conn: Connection, owner: uuid.UUID) -> DeletionRecord:
    """削除要求を受け付けて即時停止する。呼び出し側で招待とプロフィールを行ロック済みであること。"""
    conn.execute(
        text(
            "insert into public.profiles (id, display_name) values (:id, 'Alter user') "
            "on conflict (id) do nothing"
        ),
        {"id": owner},
    )
    conn.execute(
        text("update public.profiles set deleted_at = coalesce(deleted_at, now()) where id = :id"),
        {"id": owner},
    )
    paused = stop_agents_for_owner(conn, owner)
    cancelled = 0
    for agent_id in conn.execute(
        text("select id from public.agents where owner_id = :owner order by id"),
        {"owner": owner},
    ).scalars():
        cancelled += cancel_queued_interactions(conn, agent_id)
    cancel_pending_deliveries(conn, owner)
    conn.execute(
        text(
            "update public.invitations set status = 'revoked', revoked_at = now() "
            "where auth_user_id = :owner and status = 'active'"
        ),
        {"owner": owner},
    )
    row = conn.execute(
        text(
            "insert into public.deletion_requests (profile_id) values (:owner) "
            "returning id, status, requested_at, completed_at"
        ),
        {"owner": owner},
    ).one()
    record = DeletionRecord(row.id, row.status, row.requested_at, row.completed_at)
    conn.execute(
        text(
            "insert into public.background_jobs (kind, dedupe_key, payload) "
            "values (:kind, :key, cast(:payload as jsonb))"
        ),
        {
            "kind": PURGE_JOB_KIND,
            "key": f"{PURGE_JOB_KIND}:{record.id}",
            "payload": json.dumps({"deletion_request_id": str(record.id)}),
        },
    )
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (:actor, 'account.deletion_request', 'deletion_request', :rid, "
            "cast(:metadata as jsonb))"
        ),
        {
            "actor": owner,
            "rid": str(record.id),
            "metadata": json.dumps({"paused_agents": paused, "cancelled_interactions": cancelled}),
        },
    )
    return record
