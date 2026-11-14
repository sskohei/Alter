"""Read/delete boundary for #25; extraction and Worker consumption stay in #22.

Deletion follows PR #62: suppress every candidate from the same source, retain no text.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from alter_domain import agents


class Memory(BaseModel):
    id: uuid.UUID
    text: str
    importance: int = Field(ge=1, le=5)
    source_conversation_id: uuid.UUID | None
    created_at: datetime
    status: Literal["active"]


class Relationship(BaseModel):
    other_agent_id: uuid.UUID
    other_agent_name: str
    conversation_count: int = Field(ge=0)
    shared_interests: list[str]
    last_met_at: datetime | None
    # Only the caller's block, never a claim that the other participant unblocked them.
    blocked: bool


def forget(conn: Connection, owner: uuid.UUID, memory_id: uuid.UUID) -> None:
    row = conn.execute(
        text(
            "select m.agent_id,m.dedupe_key,m.source_conversation_id from public.memories m "
            "join public.agents a on a.id=m.agent_id "
            "where m.id=:id and a.owner_id=:owner and a.status<>'deleted'"
        ),
        {"id": memory_id, "owner": owner},
    ).first()
    if row is None:
        replay = conn.execute(
            text(
                "select 1 from public.audit_events where actor_profile_id=:owner "
                "and action='memory.delete' and resource_type='memory' and resource_id=:id"
            ),
            {"id": str(memory_id), "owner": owner},
        ).first()
        if replay:
            return
        raise agents.AgentNotFound()
    # Same lock order as the insertion guard: agent before memory rows.
    agents.owned(conn, owner, row.agent_id, lock=True)
    conn.execute(
        text(
            "insert into public.memory_tombstones(agent_id,dedupe_key) values (:agent,:key) "
            "on conflict do nothing"
        ),
        {"agent": row.agent_id, "key": row.dedupe_key},
    )
    if row.source_conversation_id:
        conn.execute(
            text(
                "insert into public.memory_source_suppressions(agent_id,source_conversation_id) "
                "values (:agent,:source) on conflict do nothing"
            ),
            {"agent": row.agent_id, "source": row.source_conversation_id},
        )
        conn.execute(
            text(
                "insert into public.memory_tombstones(agent_id,dedupe_key) "
                "select agent_id,dedupe_key from public.memories where agent_id=:agent "
                "and source_conversation_id=:source on conflict do nothing"
            ),
            {"agent": row.agent_id, "source": row.source_conversation_id},
        )
        conn.execute(
            text(
                "delete from public.memories where agent_id=:agent and "
                "source_conversation_id=:source"
            ),
            {"agent": row.agent_id, "source": row.source_conversation_id},
        )
    else:
        conn.execute(text("delete from public.memories where id=:id"), {"id": memory_id})
    conn.execute(
        text(
            "insert into public.audit_events(actor_profile_id,action,resource_type,resource_id) "
            "values (:owner,'memory.delete','memory',:id)"
        ),
        {"owner": owner, "id": str(memory_id)},
    )
