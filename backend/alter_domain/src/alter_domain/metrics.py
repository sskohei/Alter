"""Aggregate-only measurement. No prompts, memory text, identities or tokens are exported."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Connection, text


class AISnapshot(BaseModel):
    measured_at: datetime
    environment: Literal["local", "production"] = "local"
    observation_days: int = Field(default=0, ge=0)
    queued_jobs: int
    oldest_wait_seconds: float
    completed_conversations: int
    failed_conversations: int
    cancelled_conversations: int
    active_memories: int
    max_memories_per_agent: int
    suppressed_sources: int
    unknown_calls: int
    database_connections: int
    database_size_bytes: int
    db_stats_reset: datetime | None
    block_reads: int
    block_hits: int
    deadlocks: int
    charged_usd: Decimal

    @property
    def completion_rate(self) -> float | None:
        total = (
            self.completed_conversations + self.failed_conversations + self.cancelled_conversations
        )
        return self.completed_conversations / total if total else None


def snapshot(
    conn: Connection,
    *,
    environment: Literal["local", "production"] = "local",
    observation_days: int = 0,
) -> AISnapshot:
    jobs = conn.execute(
        text(
            "select count(*) as n,coalesce(max(extract(epoch from now()-created_at)),0) as oldest "
            "from public.background_jobs where kind='conversation' and status in "
            "('queued','retry_wait')"
        )
    ).one()
    conversations: dict[str, int] = {
        row[0]: row[1]
        for row in conn.execute(
            text("select status,count(*) from public.conversations group by status")
        )
    }
    memories = conn.execute(
        text(
            "select count(*) as n,coalesce(max(n),0) as largest from "
            "(select count(*) as n from public.memories where status='active' group by agent_id) m"
        )
    ).one()
    # Sum count is separate from number of agents.
    memory_count = conn.execute(
        text("select count(*) from public.memories where status='active'")
    ).scalar_one()
    db = conn.execute(
        text(
            "select numbackends,stats_reset,blks_read,blks_hit,deadlocks "
            "from pg_stat_database where datname=current_database()"
        )
    ).one()
    return AISnapshot(
        measured_at=datetime.now(UTC),
        environment=environment,
        observation_days=observation_days,
        queued_jobs=jobs.n,
        oldest_wait_seconds=max(0, float(jobs.oldest)),
        completed_conversations=conversations.get("completed", 0),
        failed_conversations=conversations.get("failed", 0),
        cancelled_conversations=conversations.get("cancelled", 0),
        active_memories=memory_count,
        max_memories_per_agent=memories.largest,
        suppressed_sources=conn.execute(
            text("select count(*) from public.memory_source_suppressions")
        ).scalar_one(),
        unknown_calls=conn.execute(
            text(
                "select count(*) from public.llm_invocations i join public.conversations c "
                "on c.id=i.conversation_id where i.status='unknown' "
                "or (i.status='started' and c.status='failed')"
            )
        ).scalar_one(),
        database_connections=db.numbackends,
        database_size_bytes=conn.execute(
            text("select pg_database_size(current_database())")
        ).scalar_one(),
        db_stats_reset=db.stats_reset,
        block_reads=db.blks_read,
        block_hits=db.blks_hit,
        deadlocks=db.deadlocks,
        charged_usd=conn.execute(
            text("select coalesce(sum(actual_usd),0) from public.usage_records where kind='charge'")
        ).scalar_one(),
    )


def recall_at_k(relevant: set[str], retrieved: list[str], k: int = 5) -> float | None:
    if k < 1:
        raise ValueError("k must be positive")
    if not relevant:
        return None
    return len(relevant & set(retrieved[:k])) / len(relevant)


def evaluation_gates(
    data: AISnapshot, *, recall: float | None = None, labeled_cases: int = 0
) -> dict[str, str]:
    if recall is not None and not 0 <= recall <= 1:
        raise ValueError("recall must be between zero and one")
    if data.environment != "production" or data.observation_days < 7:
        return {"memory_search": "insufficient_evidence", "job_queue": "insufficient_evidence"}
    memory = "insufficient_evidence"
    if recall is not None and labeled_cases >= 30:
        memory = (
            "evaluate" if data.max_memories_per_agent > 300 and recall < 0.8 else "keep_current"
        )
    queue = "evaluate" if data.oldest_wait_seconds > 300 else "keep_current"
    return {"memory_search": memory, "job_queue": queue}
