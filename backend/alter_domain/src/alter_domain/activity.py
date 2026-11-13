"""会話一覧・詳細と Web 通知の共通ドメイン処理（#21、docs/07・docs/15）。

参加者認可:
- 本人の AI が参加している会話だけを返す。それ以外の会話 ID は存在を隠す（API は 404）
- 参加者の利用者が削除要求済み、または AI が削除済みの会話は、要求時点から双方の一覧・詳細から外す
  （進行中の削除要求 ``deletion_requests`` の確認は #30 で同じ条件に加える）
- 発言は ``moderation_status='allowed'`` だけを ``turn_no`` 順に返す

``cost_usd``（#47 で合意）: 本人の AI・この会話・``kind='charge'`` の ``actual_usd`` の合計。
会話が ``queued`` / ``running`` の間は確定前として ``None``、終了後は合計（課金なしは 0）。
"""

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Row, text

NOTIFICATION_CONVERSATION_COMPLETED = "conversation.completed"
_OPEN_STATUSES = frozenset({"queued", "running"})

# 参加者のどちらかが削除要求済み・AI 削除済みなら、その会話を双方から隠す
_VISIBLE_CONVERSATION = (
    "not exists (select 1 from public.conversation_participants hp "
    "join public.agents ha on ha.id = hp.agent_id "
    "join public.profiles hu on hu.id = ha.owner_id "
    "where hp.conversation_id = c.id "
    "and (hu.deleted_at is not null or ha.status = 'deleted'))"
)


class NotFound(Exception):
    """本人の会話・通知ではない、または存在しない（API は 404 で存在を隠す）。"""


class InvalidCursor(ValueError):
    """解釈できない cursor（API は 422）。"""


@dataclass(frozen=True)
class AgentRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class ConversationRecord:
    id: uuid.UUID
    status: str
    partner: AgentRef
    started_at: datetime | None
    summary: str | None
    visibility: str
    cost_usd: Decimal | None
    created_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    id: uuid.UUID
    agent_id: uuid.UUID
    turn_no: int
    content: str
    created_at: datetime


@dataclass(frozen=True)
class ConversationDetailRecord:
    conversation: ConversationRecord
    participants: list[AgentRef]
    messages: list[MessageRecord]


@dataclass(frozen=True)
class NotificationRecord:
    id: uuid.UUID
    kind: str
    body: str
    created_at: datetime
    read_at: datetime | None
    conversation_id: uuid.UUID | None


# -- cursor（作成日時の新しい順・同時刻は ID 順）---------------------------------


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        created, row_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode().split("|")
        created_at = datetime.fromisoformat(created)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return created_at, uuid.UUID(row_id)
    except (ValueError, UnicodeError) as exc:
        raise InvalidCursor("invalid cursor") from exc


# -- 会話 -------------------------------------------------------------------------


def _own_agent(conn: Connection, owner: uuid.UUID) -> uuid.UUID | None:
    return conn.execute(
        text("select id from public.agents where owner_id = :owner and status <> 'deleted'"),
        {"owner": owner},
    ).scalar_one_or_none()


_CONVERSATION_SELECT = (
    "select c.id, c.status, c.started_at, c.summary, c.visibility, c.created_at, "
    "  other.id as partner_id, other.name as partner_name, "
    "  (select coalesce(sum(u.actual_usd), 0) from public.usage_records u "
    "   where u.conversation_id = c.id and u.agent_id = :agent and u.kind = 'charge') as own_cost "
    "from public.conversations c "
    "join public.conversation_participants me "
    "  on me.conversation_id = c.id and me.agent_id = :agent "
    "join public.conversation_participants op "
    "  on op.conversation_id = c.id and op.agent_id <> :agent "
    "join public.agents other on other.id = op.agent_id "
    f"where {_VISIBLE_CONVERSATION} "
)


def _conversation(row: Row[Any]) -> ConversationRecord:
    return ConversationRecord(
        id=row.id,
        status=row.status,
        partner=AgentRef(row.partner_id, row.partner_name),
        started_at=row.started_at,
        summary=row.summary,
        visibility=row.visibility,
        cost_usd=None if row.status in _OPEN_STATUSES else Decimal(row.own_cost),
        created_at=row.created_at,
    )


def list_conversations(
    conn: Connection, owner: uuid.UUID, *, limit: int, cursor: str | None
) -> tuple[list[ConversationRecord], str | None]:
    agent = _own_agent(conn, owner)
    if agent is None:
        if cursor is not None:
            decode_cursor(cursor)
        return [], None
    params: dict[str, Any] = {"agent": agent, "limit": limit + 1}
    after = ""
    if cursor is not None:
        params["created"], params["after_id"] = decode_cursor(cursor)
        after = "and (c.created_at, c.id) < (:created, :after_id) "
    rows = conn.execute(
        text(_CONVERSATION_SELECT + after + "order by c.created_at desc, c.id desc limit :limit"),
        params,
    ).all()
    items = [_conversation(row) for row in rows[:limit]]
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return items, next_cursor


def get_conversation(
    conn: Connection, owner: uuid.UUID, conversation_id: uuid.UUID
) -> ConversationDetailRecord:
    agent = _own_agent(conn, owner)
    if agent is None:
        raise NotFound()
    row = conn.execute(
        text(_CONVERSATION_SELECT + "and c.id = :id"),
        {"agent": agent, "id": conversation_id},
    ).first()
    if row is None:
        raise NotFound()
    participants = [
        AgentRef(p.id, p.name)
        for p in conn.execute(
            text(
                "select a.id, a.name from public.conversation_participants p "
                "join public.agents a on a.id = p.agent_id "
                "where p.conversation_id = :id order by p.ordinal"
            ),
            {"id": conversation_id},
        )
    ]
    messages = [
        MessageRecord(m.id, m.agent_id, m.turn_no, m.content, m.created_at)
        for m in conn.execute(
            text(
                "select id, agent_id, turn_no, content, created_at from public.messages "
                "where conversation_id = :id and moderation_status = 'allowed' order by turn_no"
            ),
            {"id": conversation_id},
        )
    ]
    return ConversationDetailRecord(_conversation(row), participants, messages)


# -- 通知 ---------------------------------------------------------------------------


def _render_body(kind: str, partner_name: str | None) -> str:
    """表示用の文字列を組み立てる。保存した body（JSON）や会話本文はそのまま返さない。"""
    if kind == NOTIFICATION_CONVERSATION_COMPLETED:
        if partner_name:
            return f"{partner_name}との交流が完了しました。"
        return "交流が完了しました。"
    return "お知らせがあります。"


_NOTIFICATION_SELECT = (
    "select n.id, n.kind, n.created_at, n.read_at, n.conversation_id, "
    "  (select other.name from public.conversation_participants op "
    "   join public.agents other on other.id = op.agent_id "
    "   where op.conversation_id = n.conversation_id and other.owner_id <> :owner "
    "   limit 1) as partner_name "
    "from public.notifications n "
    "left join public.conversations c on c.id = n.conversation_id "
    "where n.profile_id = :owner "
    f"and (n.conversation_id is null or {_VISIBLE_CONVERSATION}) "
)


def _notification(row: Row[Any]) -> NotificationRecord:
    return NotificationRecord(
        id=row.id,
        kind=row.kind,
        body=_render_body(row.kind, row.partner_name),
        created_at=row.created_at,
        read_at=row.read_at,
        conversation_id=row.conversation_id,
    )


def list_notifications(
    conn: Connection,
    owner: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
    unread: bool | None,
) -> tuple[list[NotificationRecord], str | None]:
    params: dict[str, Any] = {"owner": owner, "limit": limit + 1}
    extra = ""
    if unread is True:
        extra += "and n.read_at is null "
    elif unread is False:
        extra += "and n.read_at is not null "
    if cursor is not None:
        params["created"], params["after_id"] = decode_cursor(cursor)
        extra += "and (n.created_at, n.id) < (:created, :after_id) "
    rows = conn.execute(
        text(_NOTIFICATION_SELECT + extra + "order by n.created_at desc, n.id desc limit :limit"),
        params,
    ).all()
    items = [_notification(row) for row in rows[:limit]]
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return items, next_cursor


def mark_notification_read(
    conn: Connection, owner: uuid.UUID, notification_id: uuid.UUID
) -> NotificationRecord:
    """既読にする。再送しても最初の ``read_at`` を保つ（冪等）。"""
    updated = conn.execute(
        text(
            "update public.notifications set read_at = coalesce(read_at, now()) "
            "where id = :id and profile_id = :owner returning id"
        ),
        {"id": notification_id, "owner": owner},
    ).first()
    if updated is None:
        raise NotFound()
    row = conn.execute(
        text(_NOTIFICATION_SELECT + "and n.id = :id"),
        {"owner": owner, "id": notification_id},
    ).first()
    if row is None:
        # 既読にはできたが、会話が削除要求で非表示になった通知
        raise NotFound()
    return _notification(row)


def notify_conversation_completed(conn: Connection, conversation_id: uuid.UUID) -> int:
    """会話完了の Web 通知を参加者の利用者へ作る（完了トランザクションから呼ぶ）。

    - ``completed`` の会話だけが対象。再実行しても ``dedupe_key`` で 1 件だけ（冪等）
    - Web 通知を OFF にしている利用者、削除要求済みの利用者、削除済み AI には作らない
    - ``body`` は空の JSON。表示文言は読み出し時に組み立て、会話本文を通知に複製しない
    作った通知の件数を返す。
    """
    return int(
        conn.execute(
            text(
                "insert into public.notifications "
                "(profile_id, kind, conversation_id, body, dedupe_key) "
                "select u.id, cast(:kind as varchar), c.id, '{}'::jsonb, "
                "cast(:kind as text) || ':' || c.id::text "
                "from public.conversations c "
                "join public.conversation_participants p on p.conversation_id = c.id "
                "join public.agents a on a.id = p.agent_id and a.status <> 'deleted' "
                "join public.profiles u on u.id = a.owner_id "
                "  and u.deleted_at is null and u.web_notifications "
                "where c.id = :id and c.status = 'completed' "
                "on conflict (profile_id, dedupe_key) do nothing"
            ),
            {"id": conversation_id, "kind": NOTIFICATION_CONVERSATION_COMPLETED},
        ).rowcount
    )
