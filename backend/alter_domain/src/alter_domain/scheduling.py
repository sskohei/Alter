import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from alter_domain.budget import BudgetExceeded, reserve
from alter_domain.interaction import eligible_agents, lock_interactions, pair_allowed
from alter_domain.llm_contract import ModelPrice


def utc_slot(now: datetime) -> tuple[datetime, int]:
    if now.tzinfo is None:
        raise ValueError("An aware UTC time is required")
    utc = now.astimezone(UTC)
    return utc, (utc.hour * 60 + utc.minute) // 15


def select_due(
    conn: Connection, now: datetime, price: ModelPrice, global_limit: Decimal, *, seed: int = 0
) -> uuid.UUID | None:
    now, slot = utc_slot(now)
    lock_interactions(conn)
    agents = eligible_agents(conn)
    choices = []
    for a, b in itertools.combinations(agents, 2):
        pair = (a["id"], b["id"])
        if not pair_allowed(conn, pair, now):
            continue
        count = (
            conn.execute(
                text(
                    "select conversation_count from public.agent_relationships "
                    "where agent_low_id=:a and agent_high_id=:b"
                ),
                {"a": pair[0], "b": pair[1]},
            ).scalar_one_or_none()
            or 0
        )
        score = len(set(a["interests"]) & set(b["interests"])) + (1 if not count else 0) - count
        tie = hashlib.sha256(f"{seed}:{pair[0]}:{pair[1]}".encode()).hexdigest()
        choices.append((-score, tie, a, b))
    for _, _, a, b in sorted(choices, key=lambda x: (x[0], x[1])):
        key = f"{now.date()}:{slot}:{a['id']}:{b['id']}"
        if conn.execute(
            text(
                "select 1 from public.background_jobs where kind='conversation' and dedupe_key=:key"
            ),
            {"key": key},
        ).first():
            continue
        try:
            with conn.begin_nested():
                job, conversation = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    text(
                        "insert into public.background_jobs (id,kind,dedupe_key,payload,run_at) "
                        "values (:id,'conversation',:key,cast(:payload as jsonb),:now)"
                    ),
                    {
                        "id": job,
                        "key": key,
                        "payload": json.dumps({"conversation_id": str(conversation), "seed": seed}),
                        "now": now,
                    },
                )
                versions = {str(row["id"]): row["approved_profile_version"] for row in (a, b)}
                conn.execute(
                    text(
                        "insert into public.conversations (id,job_id,profile_versions,created_at) "
                        "values (:id,:job,cast(:versions as jsonb),:now)"
                    ),
                    {"id": conversation, "job": job, "versions": json.dumps(versions), "now": now},
                )
                for ordinal, row in enumerate((a, b), 1):
                    conn.execute(
                        text(
                            "insert into public.conversation_participants "
                            "(conversation_id,agent_id,ordinal,discord_consent,web_consent) "
                            "values (:conv,:agent,:ordinal,false,true)"
                        ),
                        {"conv": conversation, "agent": row["id"], "ordinal": ordinal},
                    )
                    reserve(
                        conn,
                        row["id"],
                        conversation,
                        price,
                        f"{conversation}:{row['id']}:reserve",
                        row["max_session_usd"],
                        global_limit,
                        now=now,
                    )
                return job
        except BudgetExceeded:
            # Savepoint rolls back both reservations, participants, conversation and job.
            continue
    return None


def schedule(
    engine: Engine,
    now: datetime,
    price: ModelPrice,
    global_limit: Decimal,
    *,
    batch_size: int = 10,
    seed: int = 0,
) -> list[uuid.UUID]:
    jobs = []
    for _ in range(max(0, min(batch_size, 10))):
        with engine.begin() as conn:
            job = select_due(conn, now, price, global_limit, seed=seed)
        if job is None:
            break
        jobs.append(job)
    return jobs
