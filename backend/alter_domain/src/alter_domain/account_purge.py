"""アカウント削除のパージ（#30 後半、docs/06・docs/23「保持・削除」）。

受付（``account_deletion.request_deletion``）が積んだジョブ ``account.purge`` を 1 件ずつ処理する。
管理鍵（Auth・Storage）を使うため、API 側の運営 CLI（``alter-api-purge``）からだけ呼ぶ。
Worker・Bot には管理鍵を渡さない（ADR-018）。

1 件の流れ（どの段階も再実行してよい。途中で失敗したらジョブを再試行に戻す）
1. 取り出し: ``for update skip locked`` でジョブを 1 件取り、``running`` とリース期限を付ける
2. DB: 1 トランザクションで、共有会話（本文・参加者・記憶・通知・配信行は CASCADE）→ 本人の AI
   （人格・設定・記憶・関係性・ブロック・同意は CASCADE）→ 監査の匿名化 → プロフィール
   （通知・連携・アイコン台帳などは CASCADE）を削除する。会話を消す前に Discord の
   スレッド ID だけを ``discord_cleanup_tasks`` へ退避する。費用台帳は保留を解放してから
   AI 削除で匿名化（agent_id / conversation_id が NULL）して残す
3. Storage: ``agent-icons`` と ``profile-private`` の本人 UUID 配下を、台帳にないものも
   含めて削除する
4. Auth: Supabase Auth のユーザーを削除する（既にいなければ成功扱い）
5. 完了: 招待と許可 identity を削除し、削除要求を ``completed`` にする

ロック順は API の認可・Scheduler（#60）と同じ「招待 → プロフィール → AI → 予算」のあと、
Discord の配信行。未確定の LLM 呼び出しや、Bot が送信中の投稿が残っている間は消さずに
再試行する（費用の保留を勝手に解放しない・作成中のスレッドを取りこぼさない）。

ジョブの更新は取り出しごとの ``lease_token`` で所有を確かめる。リース切れのあとに
取り直した実行がある場合、古い実行は結果を書かない（完了した状態を上書きしない）。
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import Connection, Engine, text

from alter_domain import budget
from alter_domain.account_deletion import PURGE_JOB_KIND

logger = logging.getLogger("alter_domain.account_purge")

STORAGE_BUCKETS = ("agent-icons", "profile-private")
LEASE_MINUTES = 15
MAX_ATTEMPTS = 8
RETENTION_DAYS = 30
HTTP_TIMEOUT_SECONDS = 10.0
STORAGE_PAGE = 1000
# 1 回の実行で一覧と削除を繰り返す上限（削除が効かない応答で無限に回らない）
STORAGE_MAX_ROUNDS = 50


class PurgeBlocked(Exception):
    """今は消せない（例: LLM 呼び出しの結果が未確定）。時間をおいて再試行する。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ExternalUnavailable(Exception):
    """Auth / Storage の管理 API に失敗した。応答本文・鍵は持たない。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LeaseLost(Exception):
    """リースが切れて別の実行がジョブを取り直した。この実行は結果を書かずに終わる。"""


class AuthDeleter(Protocol):
    def delete_user(self, user_id: uuid.UUID) -> None: ...


class StoragePurger(Protocol):
    def purge_prefix(self, bucket: str, owner: uuid.UUID) -> int: ...


@dataclass(frozen=True)
class PurgeJob:
    job_id: uuid.UUID
    request_id: uuid.UUID
    profile_id: uuid.UUID
    attempts: int
    lease_token: uuid.UUID


# -- ジョブの取り出しと結果 ----------------------------------------------------------------


def claim_job(conn: Connection, now: datetime) -> PurgeJob | None:
    """実行できるジョブを 1 件取り、``running`` にする。リース切れの ``running`` も取り直す。"""
    row = conn.execute(
        text(
            "select j.id, j.attempts, r.id as request_id, r.profile_id "
            "from public.background_jobs j "
            "join public.deletion_requests r "
            "  on r.id = cast(j.payload ->> 'deletion_request_id' as uuid) "
            "where j.kind = :kind and r.status in ('pending', 'running') and ("
            "  (j.status in ('queued', 'retry_wait') and j.run_at <= :now) "
            "  or (j.status = 'running' and j.lease_until < :now)) "
            "order by j.run_at, j.id for update of j skip locked limit 1"
        ),
        {"kind": PURGE_JOB_KIND, "now": now},
    ).first()
    if row is None:
        return None
    token = uuid.uuid4()
    conn.execute(
        text(
            "update public.background_jobs set status = 'running', attempts = attempts + 1, "
            "lease_until = :lease, lease_token = :token, last_error = null where id = :id"
        ),
        {"id": row.id, "lease": now + timedelta(minutes=LEASE_MINUTES), "token": token},
    )
    conn.execute(
        text(
            "update public.deletion_requests set status = 'running' "
            "where id = :id and status = 'pending'"
        ),
        {"id": row.request_id},
    )
    return PurgeJob(row.id, row.request_id, row.profile_id, row.attempts + 1, token)


def _ensure_owned(conn: Connection, job: PurgeJob) -> None:
    """この実行がまだジョブを持っていることを確かめ、行をロックする。違えば ``LeaseLost``。"""
    owned = conn.execute(
        text(
            "select 1 from public.background_jobs "
            "where id = :id and status = 'running' and lease_token = :token for update"
        ),
        {"id": job.job_id, "token": job.lease_token},
    ).first()
    if owned is None:
        raise LeaseLost()


def mark_failed(conn: Connection, job: PurgeJob, code: str, now: datetime) -> None:
    """失敗を記録する。``MAX_ATTEMPTS`` 回までは間隔をあけて再試行、超えたら ``failed``。

    ``failed`` は運営（sskohei）が確認して利用者へ連絡する（docs/23）。
    """
    code = code[:64]
    try:
        _ensure_owned(conn, job)
    except LeaseLost:
        # 取り直した実行が結果を書く（完了・再試行を古い実行が上書きしない）
        return
    if job.attempts >= MAX_ATTEMPTS:
        conn.execute(
            text(
                "update public.background_jobs set status = 'failed', lease_until = null, "
                "lease_token = null, last_error = :code where id = :id"
            ),
            {"id": job.job_id, "code": code},
        )
        conn.execute(
            text(
                "update public.deletion_requests set status = 'failed', error_code = :code "
                "where id = :id and status in ('pending', 'running')"
            ),
            {"id": job.request_id, "code": code},
        )
        return
    # 再試行の間隔は 2^回数 分（最大 6 時間）。30 日の期限内に十分な回数を試せる
    delay = timedelta(minutes=min(2**job.attempts, 360))
    conn.execute(
        text(
            "update public.background_jobs set status = 'retry_wait', lease_until = null, "
            "lease_token = null, last_error = :code, run_at = :run_at where id = :id"
        ),
        {"id": job.job_id, "code": code, "run_at": now + delay},
    )
    conn.execute(
        text("update public.deletion_requests set error_code = :code where id = :id"),
        {"id": job.request_id, "code": code},
    )


def complete(conn: Connection, job: PurgeJob, now: datetime) -> None:
    """招待と許可 identity を削除し、削除要求とジョブを完了にする。"""
    _ensure_owned(conn, job)
    conn.execute(
        text("delete from public.invitations where auth_user_id = :p"), {"p": job.profile_id}
    )
    conn.execute(
        text(
            "update public.deletion_requests set status = 'completed', completed_at = :now, "
            "error_code = null where id = :id and status in ('pending', 'running')"
        ),
        {"id": job.request_id, "now": now},
    )
    conn.execute(
        text(
            "update public.background_jobs set status = 'completed', lease_until = null, "
            "lease_token = null, last_error = null where id = :id"
        ),
        {"id": job.job_id},
    )


# -- DB の削除 -------------------------------------------------------------------------------


def purge_database(conn: Connection, owner: uuid.UUID) -> None:
    """本人の DB データを 1 トランザクションで削除する（何度呼んでもよい）。"""
    # ロック順: 招待 → プロフィール → AI → 予算（API の認可・Scheduler #60 と同じ）
    conn.execute(
        text("select id from public.invitations where auth_user_id = :p for update"),
        {"p": owner},
    ).all()
    profile = conn.execute(
        text("select deleted_at from public.profiles where id = :p for update"), {"p": owner}
    ).first()
    if profile is not None and profile.deleted_at is None:
        # 受付を経ていない利用者は消さない（取り違えの防止）
        raise PurgeBlocked("not_requested")
    agents = list(
        conn.execute(
            text("select id from public.agents where owner_id = :p order by id for update"),
            {"p": owner},
        ).scalars()
    )
    budget.lock_budget(conn)
    conversations = list(
        conn.execute(
            text(
                "select distinct conversation_id from public.conversation_participants "
                "where agent_id = any(cast(:a as uuid[])) order by conversation_id"
            ),
            {"a": agents},
        ).scalars()
    )
    try:
        for agent in agents:
            budget.require_settled(conn, agent=agent)
        for conversation in conversations:
            # 相手の AI の呼び出しが未確定の会話も、確定するまで消さない
            budget.require_settled(conn, conversation=conversation)
    except budget.UnsettledBudget:
        raise PurgeBlocked("llm_unsettled") from None

    # Bot が送信中（sending）の投稿があれば待つ。スレッドを作ってから ID を保存するまでの
    # 間に会話を消すと、配信行だけが消えて ID の保存が空振りし、スレッドが削除対象から漏れる。
    # 取り出し中の行は for update で待つ（取り出しが確定すると sending として読み直す）。
    # 送信中のまま止まった行は 10 分で needs_review になる（discord_delivery.recover_stale_sending）
    in_flight = conn.execute(
        text(
            "select status from public.discord_deliveries "
            "where conversation_id = any(cast(:c as uuid[])) and status in ('pending', 'sending') "
            "order by id for update"
        ),
        {"c": conversations},
    ).scalars()
    if "sending" in set(in_flight):
        raise PurgeBlocked("discord_sending")

    # Discord のスレッド ID だけを退避（会話の CASCADE で配信行が消える前に）
    conn.execute(
        text(
            "insert into public.discord_cleanup_tasks (thread_id, channel_id) "
            "select discord_thread_id, channel_id from public.discord_deliveries "
            "where conversation_id = any(cast(:c as uuid[])) and discord_thread_id is not null "
            "on conflict (thread_id) do nothing"
        ),
        {"c": conversations},
    )
    for conversation in conversations:
        budget.release_for_deletion(conn, conversation=conversation)
        conn.execute(text("delete from public.conversations where id = :c"), {"c": conversation})
    _delete_related_jobs(conn, owner, agents, conversations)

    _anonymize_audit(conn, owner, agents, conversations)
    for agent in agents:
        budget.release_for_deletion(conn, agent=agent)
        conn.execute(text("delete from public.agents where id = :a"), {"a": agent})
    # 通知・Discord 連携・OAuth state・冪等記録・アイコン台帳は CASCADE
    conn.execute(text("delete from public.profiles where id = :p"), {"p": owner})


def _delete_related_jobs(
    conn: Connection,
    owner: uuid.UUID,
    agents: list[uuid.UUID],
    conversations: list[uuid.UUID],
) -> None:
    """dedupe_key・payload に本人・本人の AI・共有会話の ID を含むジョブを消す。

    会話のジョブ（conversation.run）は dedupe_key に双方の AI ID、payload に会話 ID を持つ。
    会話が先に消えて conversations.job_id で辿れないものも、ID の一致で消える。削除ジョブ
    自身は削除要求の ID だけを持つので対象外（保持期限の削除で消える）。
    """
    ids = [str(owner), *(str(a) for a in agents), *(str(c) for c in conversations)]
    conn.execute(
        text(
            "delete from public.background_jobs where kind <> :purge "
            "and exists (select 1 from unnest(cast(:ids as text[])) i "
            "  where strpos(dedupe_key, i) > 0 or strpos(payload::text, i) > 0)"
        ),
        {"purge": PURGE_JOB_KIND, "ids": ids},
    )


def _anonymize_audit(
    conn: Connection, owner: uuid.UUID, agents: list[uuid.UUID], conversations: list[uuid.UUID]
) -> None:
    """監査の actor・resource・metadata から本人を辿れる識別子を外す（行と操作の種類は残す）。"""
    ids = [str(owner), *(str(a) for a in agents), *(str(c) for c in conversations)]
    conn.execute(
        text(
            "update public.audit_events set "
            "actor_profile_id = case when actor_profile_id = :owner then null "
            "  else actor_profile_id end, "
            "resource_id = case when resource_id = any(cast(:ids as text[])) "
            "  then 'redacted' else resource_id end, "
            "metadata = case when exists (select 1 from unnest(cast(:ids as text[])) i "
            "  where strpos(metadata::text, i) > 0) then '{}'::jsonb else metadata end "
            "where actor_profile_id = :owner or resource_id = any(cast(:ids as text[])) "
            "or exists (select 1 from unnest(cast(:ids as text[])) i "
            "  where strpos(metadata::text, i) > 0)"
        ),
        {"owner": owner, "ids": ids},
    )


# -- 1 件の実行 -----------------------------------------------------------------------------


def run_one(
    engine: Engine, auth: AuthDeleter, storage: StoragePurger, now: datetime
) -> PurgeJob | None:
    """ジョブを 1 件処理する。なければ None。失敗はジョブに記録して返す（例外を外へ出さない）。"""
    with engine.begin() as conn:
        job = claim_job(conn, now)
    if job is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_owned(conn, job)
            purge_database(conn, job.profile_id)
        for bucket in STORAGE_BUCKETS:
            storage.purge_prefix(bucket, job.profile_id)
        auth.delete_user(job.profile_id)
        with engine.begin() as conn:
            complete(conn, job, now)
        logger.info("account purge completed: %s", job.request_id)
    except LeaseLost:
        logger.warning("account purge lease lost: %s", job.request_id)
    except (PurgeBlocked, ExternalUnavailable) as exc:
        logger.warning("account purge retry: %s (%s)", job.request_id, exc.code)
        with engine.begin() as conn:
            mark_failed(conn, job, exc.code, now)
    except Exception:  # noqa: BLE001 - 中身（個人情報を含み得る）は残さず固定コードにする
        logger.exception("account purge failed: %s", job.request_id)
        with engine.begin() as conn:
            mark_failed(conn, job, "internal_error", now)
    return job


# -- 保持期限のパージ ------------------------------------------------------------------------


def purge_expired(conn: Connection, now: datetime) -> dict[str, int]:
    """削除・失効の追跡情報を期限で消す（日次）。件数だけを返す（個人情報を含まない）。"""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    statements = {
        # 完了から 30 日たった削除要求とそのジョブ
        "background_jobs": (
            "delete from public.background_jobs where kind = :kind and status = 'completed' "
            "and cast(payload ->> 'deletion_request_id' as uuid) in ("
            "  select id from public.deletion_requests "
            "  where status = 'completed' and completed_at < :cutoff)"
        ),
        "deletion_requests": (
            "delete from public.deletion_requests where status = 'completed' "
            "and completed_at < :cutoff"
        ),
        # 失効から 30 日たった招待と許可 identity（identity は CASCADE）
        "invitations": (
            "delete from public.invitations where status = 'revoked' and revoked_at < :cutoff"
        ),
        # 解除から 30 日たった Discord 連携のメタデータ
        "discord_connections": (
            "delete from public.discord_connections where revoked_at < :cutoff"
        ),
        # 期限切れ・使用済みの OAuth state
        "discord_oauth_states": (
            "delete from public.discord_oauth_states where expires_at < :now or used_at is not null"
        ),
        # 結果を記録してから 30 日たった Discord 削除の追跡
        "discord_cleanup_tasks": (
            "delete from public.discord_cleanup_tasks where completed_at < :cutoff"
        ),
    }
    counts: dict[str, int] = {}
    for name, sql in statements.items():
        counts[name] = int(
            conn.execute(text(sql), {"cutoff": cutoff, "now": now, "kind": PURGE_JOB_KIND}).rowcount
        )
    return counts


# -- 管理 API のクライアント（API 側 CLI からだけ使う） --------------------------------------


class SupabaseAuthDeleter:
    def __init__(self, base_url: str, secret_key: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._secret = secret_key
        self._http = http

    def delete_user(self, user_id: uuid.UUID) -> None:
        if not self._secret:
            raise ExternalUnavailable("auth_key_missing")
        try:
            response = self._http.delete(
                f"{self._base}/auth/v1/admin/users/{user_id}",
                headers={"apikey": self._secret, "Authorization": f"Bearer {self._secret}"},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ExternalUnavailable("auth_unavailable") from None
        # 404: もういない（前回の実行で削除済み）
        if response.status_code not in (200, 204, 404):
            raise ExternalUnavailable(f"auth_http_{response.status_code}")


class SupabaseStoragePurger:
    def __init__(self, base_url: str, secret_key: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/") + "/storage/v1"
        self._secret = secret_key
        self._http = http

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._secret,
            "Authorization": f"Bearer {self._secret}",
            "Content-Type": "application/json",
        }

    def purge_prefix(self, bucket: str, owner: uuid.UUID) -> int:
        """``{owner}/`` 配下を全部消す（台帳にない取り残しも対象）。消した件数を返す。"""
        if not self._secret:
            raise ExternalUnavailable("storage_key_missing")
        removed = 0
        for _ in range(STORAGE_MAX_ROUNDS):
            names = self._list(bucket, owner)
            if not names:
                return removed
            try:
                response = self._http.request(
                    "DELETE",
                    f"{self._base}/object/{bucket}",
                    headers=self._headers(),
                    content=json.dumps({"prefixes": names}),
                    timeout=HTTP_TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                raise ExternalUnavailable("storage_unavailable") from None
            if response.status_code != 200:
                raise ExternalUnavailable(f"storage_http_{response.status_code}")
            removed += len(names)
        raise ExternalUnavailable("storage_not_emptied")

    def _list(self, bucket: str, owner: uuid.UUID) -> list[str]:
        try:
            response = self._http.post(
                f"{self._base}/object/list/{bucket}",
                headers=self._headers(),
                content=json.dumps({"prefix": f"{owner}/", "limit": STORAGE_PAGE, "offset": 0}),
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ExternalUnavailable("storage_unavailable") from None
        if response.status_code != 200:
            raise ExternalUnavailable(f"storage_http_{response.status_code}")
        try:
            items = response.json()
        except ValueError:
            raise ExternalUnavailable("storage_bad_response") from None
        if not isinstance(items, list):
            raise ExternalUnavailable("storage_bad_response")
        names: list[str] = []
        for item in items:
            name = item.get("name") if isinstance(item, dict) else None
            # Storage の一覧ではフォルダは id が null。フォルダ・不正な名前・隠しファイルは扱わない
            is_folder = isinstance(item, dict) and item.get("id") is None
            if (
                is_folder
                or not isinstance(name, str)
                or not name
                or "/" in name
                or name.startswith(".")
            ):
                # フォルダ（入れ子）や不正な名前は扱わない（本人の配下は 1 階層だけ）
                continue
            names.append(f"{owner}/{name}")
        return names
