"""Web 本人と Discord ユーザーの連携・解除とギルド別同意の表示（#26、docs/06・docs/07・docs/11）。

state:
- 32 バイトの乱数（URL セーフ）を返し、DB には SHA-256 の 16 進ハッシュだけを保存する
- 有効期間 10 分・使い捨て・発行した本人だけが使える。別の本人の state は消費せずに拒否する
- 期限切れ・使用済みの state は発行時に掃除する。1 人が同時に持てる未使用 state は 5 件まで

連携:
- Discord から ``identify`` で ID だけを取得し、トークンは保存しない（discord_oauth.py）
- 本人がすでに連携済み、またはその Discord ユーザーが別の本人と連携済みなら 409

解除（連携がなくても 204 の冪等）:
- 連携を失効（``revoked_at``）し、本人あての未送信 DM を取り消す
- 本人の AI のギルド別公開同意も取り消し、その AI が参加する会話の未投稿を取り消す
  （再連携しても自動では戻さない。公開し直すには Discord で ``/agent publish`` が必要）
"""

import enum
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from alter_domain.discord_oauth import DiscordCodeRejected, DiscordOAuthClient

STATE_TTL_MINUTES = 10
MAX_PENDING_STATES = 5
STATE_BYTES = 32


class AlreadyLinked(Exception):
    """本人がすでに Discord と連携済み（409）。"""


class LinkOutcome(enum.Enum):
    LINKED = "linked"
    STATE_INVALID = "state_invalid"  # 400: 不正・期限切れ・使用済み・別の本人
    CODE_REJECTED = "code_rejected"  # 400: Discord が認可コードを拒否
    ALREADY_LINKED = "already_linked"  # 409: 本人がすでに連携済み
    DISCORD_ACCOUNT_IN_USE = "discord_account_in_use"  # 409: 別の本人と連携済みの Discord


@dataclass(frozen=True)
class ConnectionRecord:
    connected: bool
    discord_user_id: str | None


@dataclass(frozen=True)
class LinkStart:
    url: str
    state_expires_at: datetime


@dataclass(frozen=True)
class GuildRecord:
    guild_id: str
    guild_name: str | None
    guild_enabled: bool
    channel_id: str | None
    channel_name: str | None
    consented_at: datetime | None
    revoked_at: datetime | None


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _audit(
    conn: Connection, actor: uuid.UUID, action: str, metadata: dict[str, Any] | None = None
) -> None:
    """監査に Discord の ID・state・コードは残さない。"""
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (:actor, :action, 'discord_connection', :rid, cast(:metadata as jsonb))"
        ),
        {
            "actor": actor,
            "action": action,
            "rid": str(actor),
            "metadata": json.dumps(metadata or {}),
        },
    )


def get_connection(conn: Connection, owner: uuid.UUID) -> ConnectionRecord:
    discord_user_id = conn.execute(
        text(
            "select discord_user_id from public.discord_connections "
            "where profile_id = :owner and revoked_at is null"
        ),
        {"owner": owner},
    ).scalar_one_or_none()
    return ConnectionRecord(discord_user_id is not None, discord_user_id)


def start_link(conn: Connection, owner: uuid.UUID, oauth: DiscordOAuthClient) -> LinkStart:
    """連携開始用の state を発行し、Discord の認可 URL を返す。"""
    if get_connection(conn, owner).connected:
        raise AlreadyLinked()
    state = secrets.token_urlsafe(STATE_BYTES)
    url = oauth.authorize_url(state)  # 未設定ならここで止める（state を作らない）
    conn.execute(
        text(
            "delete from public.discord_oauth_states "
            "where expires_at <= now() or used_at is not null"
        )
    )
    # 未使用 state は本人ごとに新しいものだけ残す（今回の 1 件を含めて上限まで）
    conn.execute(
        text(
            "delete from public.discord_oauth_states where id in ("
            "  select id from public.discord_oauth_states where profile_id = :owner "
            "  order by created_at desc, id desc offset :keep)"
        ),
        {"owner": owner, "keep": MAX_PENDING_STATES - 1},
    )
    expires_at = conn.execute(
        text(
            "insert into public.discord_oauth_states (profile_id, state_hash, expires_at) "
            "values (:owner, :hash, now() + make_interval(mins => :ttl)) returning expires_at"
        ),
        {"owner": owner, "hash": hash_state(state), "ttl": STATE_TTL_MINUTES},
    ).scalar_one()
    _audit(conn, owner, "discord.link_start")
    return LinkStart(url=url, state_expires_at=expires_at)


def _consume_state(conn: Connection, owner: uuid.UUID, state: str) -> bool:
    """本人の有効な state なら削除して True。別の本人の state は消費しない。"""
    row = conn.execute(
        text(
            "select id, profile_id, expires_at > now() as fresh, used_at "
            "from public.discord_oauth_states where state_hash = :hash for update"
        ),
        {"hash": hash_state(state)},
    ).first()
    if row is None or row.profile_id != owner:
        return False
    conn.execute(text("delete from public.discord_oauth_states where id = :id"), {"id": row.id})
    return bool(row.fresh) and row.used_at is None


def complete_link(
    conn: Connection,
    owner: uuid.UUID,
    *,
    code: str,
    state: str,
    oauth: DiscordOAuthClient,
) -> tuple[LinkOutcome, ConnectionRecord | None]:
    """state を照合・消費してから Discord でコードを交換し、連携を作る。

    失敗の結果も呼び出し側でコミットする（state の消費を巻き戻さず、使い捨てを保つ）。
    Discord に到達できない場合だけ例外（``DiscordOAuthUnavailable``）で巻き戻す。
    """
    if not _consume_state(conn, owner, state):
        _audit(conn, owner, "discord.link_failed", {"reason": "state_invalid"})
        return LinkOutcome.STATE_INVALID, None
    if get_connection(conn, owner).connected:
        _audit(conn, owner, "discord.link_failed", {"reason": "already_linked"})
        return LinkOutcome.ALREADY_LINKED, None
    try:
        discord_user_id = oauth.fetch_user_id(code)
    except DiscordCodeRejected:
        _audit(conn, owner, "discord.link_failed", {"reason": "code_rejected"})
        return LinkOutcome.CODE_REJECTED, None
    try:
        with conn.begin_nested():
            conn.execute(
                text(
                    "insert into public.discord_connections (profile_id, discord_user_id) "
                    "values (:owner, :discord_user_id)"
                ),
                {"owner": owner, "discord_user_id": discord_user_id},
            )
    except IntegrityError:
        # 同時に連携した、または別の本人がこの Discord ユーザーと連携済み
        mine = get_connection(conn, owner).connected
        outcome = LinkOutcome.ALREADY_LINKED if mine else LinkOutcome.DISCORD_ACCOUNT_IN_USE
        _audit(conn, owner, "discord.link_failed", {"reason": outcome.value})
        return outcome, None
    _audit(conn, owner, "discord.link")
    return LinkOutcome.LINKED, ConnectionRecord(True, discord_user_id)


def disconnect(conn: Connection, owner: uuid.UUID) -> bool:
    """連携を解除する。連携がなければ何もしない（冪等。解除したら True）。"""
    discord_user_id = conn.execute(
        text(
            "update public.discord_connections set revoked_at = now() "
            "where profile_id = :owner and revoked_at is null returning discord_user_id"
        ),
        {"owner": owner},
    ).scalar_one_or_none()
    if discord_user_id is None:
        return False
    conn.execute(
        text("delete from public.discord_oauth_states where profile_id = :owner"),
        {"owner": owner},
    )
    dm_cancelled = conn.execute(
        text(
            "update public.discord_dm_deliveries set status = 'cancelled' "
            "where discord_user_id = :d and status = 'pending'"
        ),
        {"d": discord_user_id},
    ).rowcount
    consents_revoked = conn.execute(
        text(
            "update public.agent_discord_consents set revoked_at = now() "
            "where revoked_at is null and agent_id in ("
            "  select id from public.agents where owner_id = :owner)"
        ),
        {"owner": owner},
    ).rowcount
    posts_cancelled = conn.execute(
        text(
            "update public.discord_deliveries d set status = 'cancelled' "
            "where d.status = 'pending' and exists ("
            "  select 1 from public.conversation_participants p "
            "  join public.agents a on a.id = p.agent_id "
            "  where p.conversation_id = d.conversation_id and a.owner_id = :owner)"
        ),
        {"owner": owner},
    ).rowcount
    _audit(
        conn,
        owner,
        "discord.unlink",
        {
            "dm_cancelled": dm_cancelled,
            "consents_revoked": consents_revoked,
            "posts_cancelled": posts_cancelled,
        },
    )
    return True


def list_guilds(conn: Connection, owner: uuid.UUID) -> list[GuildRecord]:
    """本人の AI がギルド別公開に同意した（または同意を取り消した）ギルドと投稿先。"""
    rows = conn.execute(
        text(
            "select g.id as guild_id, g.name as guild_name, g.enabled as guild_enabled, "
            "  ch.id as channel_id, ch.name as channel_name, "
            "  c.consented_at, c.revoked_at "
            "from public.agent_discord_consents c "
            "join public.agents a on a.id = c.agent_id "
            "join public.discord_guilds g on g.id = c.guild_id "
            "left join public.discord_channels ch on ch.guild_id = g.id and ch.enabled "
            "where a.owner_id = :owner and a.status <> 'deleted' "
            "order by g.name nulls last, g.id"
        ),
        {"owner": owner},
    ).all()
    return [
        GuildRecord(
            guild_id=r.guild_id,
            guild_name=r.guild_name,
            guild_enabled=r.guild_enabled,
            channel_id=r.channel_id,
            channel_name=r.channel_name,
            consented_at=r.consented_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]
