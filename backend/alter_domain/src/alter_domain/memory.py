"""Extractive MVP memories, source suppression and relationship recomputation.

No extra LLM invocation: quote only already moderated utterances, never promote them to facts.
"""

import hashlib
import unicodedata
import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, text

from alter_domain.agents import AgentNotFound
from alter_domain.interaction import lock_interactions
from alter_domain.llm import BaselineModerator


class Memory(BaseModel):
    id: uuid.UUID
    text: str
    importance: int
    source_conversation_id: uuid.UUID
    created_at: datetime
    status: str


class Relationship(BaseModel):
    other_agent_id: uuid.UUID
    other_agent_name: str
    conversation_count: int
    shared_interests: list[str]
    last_met_at: datetime | None
    blocked: bool = Field(
        description="本人のAIから相手のAIへのブロック。相手からのブロックは含めない。"
    )


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
            "where p.agent_id=:a and q.agent_id=:b and c.status='completed'"
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


def extract_completed(conn: Connection, conversation: uuid.UUID) -> None:
    # Caller must take lock_interactions first; all output/state is committed together.
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
    if len(ids) != 2:
        return
    fresh = conn.execute(
        text(
            "insert into public.memory_completions (conversation_id) values (:id) "
            "on conflict do nothing returning conversation_id"
        ),
        {"id": conversation},
    ).first()
    # Always recompute, never increment counters; retries cannot overcount.
    recompute_relationship(conn, ids[0], ids[1])
    if fresh is None:
        return
    messages = conn.execute(
        text(
            "select agent_id,content from public.messages where conversation_id=:id "
            "and moderation_status='allowed' order by turn_no"
        ),
        {"id": conversation},
    ).all()
    summary = "\n".join(
        f"AI{ids.index(m.agent_id) + 1}の会話中の発言: {m.content}" for m in messages
    )[:4000]
    if BaselineModerator().allowed(summary):
        conn.execute(
            text("update public.conversations set summary=:summary where id=:id"),
            {"id": conversation, "summary": summary},
        )
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
            if not BaselineModerator().allowed(value):
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


def enrich_pending(engine: Engine, *, limit: int = 10) -> int:
    count = 0
    for _ in range(min(max(limit, 0), 10)):
        with engine.begin() as conn:
            lock_interactions(conn)
            event = conn.execute(
                text(
                    "select e.conversation_id from public.conversation_events e "
                    "where e.kind='completed' and not exists(select 1 from "
                    "public.memory_completions m "
                    "where m.conversation_id=e.conversation_id) "
                    "order by e.created_at for update of e skip locked limit 1"
                )
            ).first()
            if event is None:
                break
            extract_completed(conn, event.conversation_id)
            count += 1
    return count


def forget(conn: Connection, owner: uuid.UUID, memory_id: uuid.UUID) -> None:
    row = conn.execute(
        text(
            "select m.agent_id,m.dedupe_key,m.source_conversation_id from public.memories m "
            "join public.agents a on a.id=m.agent_id where m.id=:id and a.owner_id=:owner "
            "and a.status<>'deleted' for update of m"
        ),
        {"id": memory_id, "owner": owner},
    ).first()
    if row is None:
        raise AgentNotFound()
    conn.execute(
        text(
            "insert into public.memory_tombstones (agent_id,dedupe_key) values (:id,:key) "
            "on conflict do nothing"
        ),
        {"id": row.agent_id, "key": row.dedupe_key},
    )
    if row.source_conversation_id:
        conn.execute(
            text(
                "insert into public.memory_source_suppressions (agent_id,source_conversation_id) "
                "values (:id,:source) on conflict do nothing"
            ),
            {"id": row.agent_id, "source": row.source_conversation_id},
        )
        conn.execute(
            text(
                "insert into public.memory_tombstones (agent_id,dedupe_key) "
                "select agent_id,dedupe_key from public.memories "
                "where agent_id=:id and source_conversation_id=:source on conflict do nothing"
            ),
            {"id": row.agent_id, "source": row.source_conversation_id},
        )
        # Suppress the whole source so summaries cannot reinsert deleted content.
        conn.execute(
            text(
                "delete from public.memories where agent_id=:id and source_conversation_id=:source"
            ),
            {"id": row.agent_id, "source": row.source_conversation_id},
        )
    else:
        conn.execute(text("delete from public.memories where id=:id"), {"id": memory_id})
    conn.execute(
        text(
            "insert into public.audit_events (actor_profile_id,action,resource_type,resource_id) "
            "values (:owner,'memory.delete','memory',:id)"
        ),
        {"owner": owner, "id": str(memory_id)},
    )


def context(conn: Connection, agent: uuid.UUID, other: uuid.UUID) -> list[str]:
    return list(
        conn.execute(
            text(
                "select m.text from public.memories m "
                "join public.conversations c on c.id=m.source_conversation_id and "
                "c.status='completed' "
                "join public.conversation_participants p on p.conversation_id=c.id and "
                "p.agent_id=:other "
                "join public.agents a on a.id=:agent join public.profiles u on u.id=a.owner_id "
                "where m.agent_id=:agent and m.status='active' and u.deleted_at is null "
                "and a.status<>'deleted' and not exists (select 1 from "
                "public.memory_source_suppressions s "
                "where s.agent_id=m.agent_id and s.source_conversation_id=c.id) "
                "and not exists (select 1 from public.conversation_participants hidden "
                "join public.agents ha on ha.id=hidden.agent_id "
                "join public.profiles hu on hu.id=ha.owner_id where hidden.conversation_id=c.id "
                "and (hu.deleted_at is not null or ha.status='deleted')) "
                "order by m.importance desc,m.created_at desc,m.id desc limit 5"
            ),
            {"agent": agent, "other": other},
        ).scalars()
    )


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


def recent_summary(conn: Connection, agent: uuid.UUID, other: uuid.UUID) -> str | None:
    return conn.execute(
        text(
            "select c.summary from public.conversations c "
            "join public.conversation_participants p on p.conversation_id=c.id and "
            "p.agent_id=:agent "
            "join public.conversation_participants q on q.conversation_id=c.id and "
            "q.agent_id=:other "
            "where c.status='completed' and c.summary is not null and not exists "
            "(select 1 from public.memory_source_suppressions s "
            "where s.agent_id=:agent and s.source_conversation_id=c.id) "
            "and not exists (select 1 from public.conversation_participants hidden "
            "join public.agents ha on ha.id=hidden.agent_id "
            "join public.profiles hu on hu.id=ha.owner_id where hidden.conversation_id=c.id "
            "and (hu.deleted_at is not null or ha.status='deleted')) "
            "order by c.ended_at desc,c.id desc limit 1"
        ),
        {"agent": agent, "other": other},
    ).scalar_one_or_none()
