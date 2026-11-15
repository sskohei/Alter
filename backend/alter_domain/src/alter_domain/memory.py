"""Extractive MVP memories, source suppression and relationship recomputation.

Candidate extraction uses no LLM. Semantic summaries are produced separately by summary.py.
"""

import hashlib
import unicodedata
import uuid
from typing import Any

from sqlalchemy import Connection, Engine, Row, text

from alter_domain.activity import _VISIBLE_CONVERSATION
from alter_domain.interaction import lock_interactions
from alter_domain.llm import SafetyModerator


def safe(value: str, forbidden: tuple[str, ...] = ()) -> bool:
    return SafetyModerator().allowed(value) and not any(key and key in value for key in forbidden)


def dedupe(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def recompute_relationship(conn: Connection, a: uuid.UUID, b: uuid.UUID) -> None:
    low, high = sorted((a, b))
    aggregate = conn.execute(
        text(
            "select count(*) as n,max(c.ended_at) as last_met "
            "from public.conversations c join public.conversation_participants p on "
            "p.conversation_id=c.id "
            "join public.conversation_participants q on q.conversation_id=c.id "
            "where p.agent_id=:a and q.agent_id=:b and c.status='completed' and "
            + _VISIBLE_CONVERSATION
        ),
        {"a": low, "b": high},
    ).one()
    if not aggregate.n:
        conn.execute(
            text(
                "delete from public.agent_relationships where agent_low_id=:a and agent_high_id=:b"
            ),
            {"a": low, "b": high},
        )
        return
    interests = list(
        conn.execute(
            text(
                "select p.interests from public.agent_profiles p join public.agents a "
                "on a.id=p.agent_id and a.approved_profile_version=p.version where a.id in (:a,:b) "
                "order by a.id"
            ),
            {"a": low, "b": high},
        ).scalars()
    )
    shared = sorted(set(interests[0]) & set(interests[1]))[:20] if len(interests) == 2 else []
    conn.execute(
        text(
            "insert into public.agent_relationships "
            "(agent_low_id,agent_high_id,conversation_count,last_met_at,shared_interests) "
            "values (:a,:b,:n,:last,:shared) on conflict (agent_low_id,agent_high_id) "
            "do update set "
            "conversation_count=excluded.conversation_count,last_met_at=excluded.last_met_at,"
            "shared_interests=excluded.shared_interests"
        ),
        {"a": low, "b": high, "n": aggregate.n, "last": aggregate.last_met, "shared": shared},
    )


def extract_completed(
    conn: Connection, conversation: uuid.UUID, *, forbidden: tuple[str, ...] = ()
) -> None:
    # Same agent-before-memory lock order as API deletion and the insertion guard.
    lock_interactions(conn)
    row = conn.execute(
        text("select status from public.conversations where id=:id for update"),
        {"id": conversation},
    ).first()
    if row is None or row.status != "completed":
        return
    ids = list(
        conn.execute(
            text(
                "select agent_id from public.conversation_participants "
                "where conversation_id=:id order by ordinal"
            ),
            {"id": conversation},
        ).scalars()
    )
    fresh = conn.execute(
        text(
            "insert into public.memory_completions (conversation_id) values (:id) "
            "on conflict do nothing returning conversation_id"
        ),
        {"id": conversation},
    ).first()
    if len(ids) != 2:
        return
    # Always recompute, never increment counters; retries cannot overcount.
    recompute_relationship(conn, ids[0], ids[1])
    if fresh is None:
        return
    visible = conn.execute(
        text("select 1 from public.conversations c where c.id=:id and " + _VISIBLE_CONVERSATION),
        {"id": conversation},
    ).first()
    if visible is None:
        return
    messages = conn.execute(
        text(
            "select agent_id,content from public.messages where conversation_id=:id "
            "and moderation_status='allowed' order by turn_no"
        ),
        {"id": conversation},
    ).all()
    messages = [m for m in messages if safe(m.content, forbidden)]
    for aid in ids:
        if conn.execute(
            text(
                "select 1 from public.memory_source_suppressions "
                "where agent_id=:agent and source_conversation_id=:conv"
            ),
            {"agent": aid, "conv": conversation},
        ).first():
            continue
        owner_active = conn.execute(
            text(
                "select 1 from public.agents a join public.profiles p on p.id=a.owner_id "
                "where a.id=:id and a.status<>'deleted' and p.deleted_at is null"
            ),
            {"id": aid},
        ).first()
        if owner_active is None:
            continue
        for message in messages[:3]:
            role = "自分のAI" if message.agent_id == aid else "相手のAI"
            value = f"会話中の{role}の発言（確定事実ではない）: {message.content[:900]}"
            if not safe(value, forbidden):
                continue
            key = dedupe(value)
            conn.execute(
                text(
                    "insert into public.memories "
                    "(agent_id,source_conversation_id,text,importance,dedupe_key) "
                    "select :agent,:conv,:text,3,:key where not exists "
                    "(select 1 from public.memory_tombstones where agent_id=:agent and "
                    "dedupe_key=:key) "
                    "on conflict (agent_id,dedupe_key) do nothing"
                ),
                {"agent": aid, "conv": conversation, "text": value, "key": key},
            )


def enrich_pending(engine: Engine, *, limit: int = 10, forbidden: tuple[str, ...] = ()) -> int:
    count = 0
    for _ in range(min(max(limit, 0), 10)):
        with engine.begin() as conn:
            lock_interactions(conn)
            event = conn.execute(
                text(
                    "select e.conversation_id from public.conversation_events e "
                    "join public.conversations c on c.id=e.conversation_id "
                    "and c.status='completed' "
                    "where e.kind='completed' and not exists(select 1 from "
                    "public.memory_completions m "
                    "where m.conversation_id=e.conversation_id) "
                    "order by e.created_at for update of e skip locked limit 1"
                )
            ).first()
            if event is None:
                break
            extract_completed(conn, event.conversation_id, forbidden=forbidden)
            count += 1
    return count


_CONTEXT_SQL = (
    "select m.text from public.memories m "
    "join public.conversations c on c.id=m.source_conversation_id and "
    "c.status='completed' "
    "join public.conversation_participants p on p.conversation_id=c.id and "
    "p.agent_id=:other "
    "join public.conversation_participants me on me.conversation_id=c.id "
    "and me.agent_id=:agent "
    "join public.agents a on a.id=:agent join public.profiles u on u.id=a.owner_id "
    "where m.agent_id=:agent and m.status='active' and u.deleted_at is null "
    "and m.created_at>now()-interval '180 days' and "
    + _VISIBLE_CONVERSATION
    + " and not exists(select 1 from public.memory_tombstones t "
    "where t.agent_id=m.agent_id and t.dedupe_key=m.dedupe_key) "
    "and a.status<>'deleted' and not exists (select 1 from "
    "public.memory_source_suppressions s "
    "where s.agent_id=m.agent_id and s.source_conversation_id=c.id) "
    "order by m.importance desc,m.created_at desc,m.id desc limit 5"
)


def context(conn: Connection, agent: uuid.UUID, other: uuid.UUID) -> list[str]:
    return list(conn.execute(text(_CONTEXT_SQL), {"agent": agent, "other": other}).scalars())


def delete_conversation(conn: Connection, conversation: uuid.UUID) -> None:
    # Internal purge hook for #30, deliberately not exposed as an unauthenticated route.
    lock_interactions(conn)
    ids = list(
        conn.execute(
            text(
                "select agent_id from public.conversation_participants "
                "where conversation_id=:id order by ordinal"
            ),
            {"id": conversation},
        ).scalars()
    )
    conn.execute(text("delete from public.conversations where id=:id"), {"id": conversation})
    if len(ids) == 2:
        recompute_relationship(conn, ids[0], ids[1])


_RECENT_SUMMARY_SQL = (
    "select c.id,c.summary,p.ordinal, "
    "array(select dedupe_key from public.memory_tombstones where agent_id=:agent) "
    "as tombstone_keys, coalesce((select jsonb_agg(jsonb_build_object("
    "'agent_id',m.agent_id::text,'content',m.content)) from public.messages m "
    "where m.conversation_id=c.id and m.moderation_status='allowed'),'[]'::jsonb) "
    "as messages from public.conversations c "
    "join public.conversation_participants p on p.conversation_id=c.id and p.agent_id=:agent "
    "join public.conversation_participants q on q.conversation_id=c.id and q.agent_id=:other "
    "where c.status='completed' and c.summary is not null and "
    + _VISIBLE_CONVERSATION
    + " and not exists(select 1 from public.memory_source_suppressions s "
    "where s.agent_id=:agent and s.source_conversation_id=c.id) "
    "order by c.ended_at desc,c.id desc limit 1"
)


def _visible_summary(row: Row[Any] | None, agent: uuid.UUID) -> str | None:
    if row is None or row.summary is None or not safe(row.summary):
        return None
    # A deleted hash may occur in another source too. Never reintroduce it via a summary.
    keys = set(row.tombstone_keys)
    for message in row.messages:
        role = "自分のAI" if message["agent_id"] == str(agent) else "相手のAI"
        value = f"会話中の{role}の発言（確定事実ではない）: {message['content'][:900]}"
        if dedupe(value) in keys:
            return None
    return (
        str(row.summary)
        .replace(f"AI{row.ordinal}の", "自分のAIの")
        .replace(f"AI{3 - row.ordinal}の", "相手のAIの")
    )


def recent_summary(conn: Connection, agent: uuid.UUID, other: uuid.UUID) -> str | None:
    row = conn.execute(text(_RECENT_SUMMARY_SQL), {"agent": agent, "other": other}).first()
    return _visible_summary(row, agent)


def prompt_context(
    conn: Connection, agent: uuid.UUID, other: uuid.UUID
) -> tuple[list[str], str | None]:
    """Load memories, summary and deletion checks in one database snapshot/round trip."""
    row = conn.execute(
        text(
            "select array(select m.text from (" + _CONTEXT_SQL + ") m) as memories,recent.* "
            "from (select 1) anchor left join lateral (" + _RECENT_SUMMARY_SQL + ") recent on true"
        ),
        {"agent": agent, "other": other},
    ).one()
    return list(row.memories), _visible_summary(row, agent)
