"""Fenced, resumable conversation Worker. External calls happen outside DB transactions."""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from alter_domain.agent_models import ProfileInput
from alter_domain.budget import consume, exposure, held, lock_budget, release_session
from alter_domain.interaction import eligible_agents, lock_interactions, pair_allowed
from alter_domain.llm import (
    LLMClient,
    Moderator,
    TemporaryLLMError,
    UnknownLLMOutcome,
    UnsafeOutput,
    build_prompt,
)
from alter_domain.llm_contract import Utterance


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class Lease:
    job: uuid.UUID
    token: uuid.UUID
    conversation: uuid.UUID
    attempts: int


def claim(conn: Connection, now: datetime) -> Lease | None:
    lock_budget(conn)
    row = conn.execute(
        text(
            "select id,attempts from public.background_jobs "
            "where kind='conversation' and ((status in ('queued','retry_wait') and run_at<=:now) "
            "or (status='running' and lease_until<=:now)) "
            "order by run_at,id for update skip locked limit 1"
        ),
        {"now": now},
    ).first()
    if row is None:
        return None
    token = uuid.uuid4()
    conn.execute(
        text(
            "update public.background_jobs set status='running',lease_token=:token,"
            "lease_until=:until,attempts=attempts+1 where id=:id"
        ),
        {"id": row.id, "token": token, "until": now + timedelta(seconds=90)},
    )
    conv = conn.execute(
        text("select id from public.conversations where job_id=:id"), {"id": row.id}
    ).scalar_one()
    return Lease(row.id, token, conv, row.attempts + 1)


def assert_lease(conn: Connection, lease: Lease, now: datetime) -> None:
    if (
        conn.execute(
            text(
                "select 1 from public.background_jobs where id=:id and status='running' "
                "and lease_token=:token and lease_until>:now for update"
            ),
            {"id": lease.job, "token": lease.token, "now": now},
        ).first()
        is None
    ):
        raise LeaseLost()


def renew(engine: Engine, lease: Lease, now: datetime) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "update public.background_jobs set lease_until=:until "
                "where id=:id and status='running' and lease_token=:token and lease_until>:now"
            ),
            {
                "id": lease.job,
                "token": lease.token,
                "now": now,
                "until": now + timedelta(seconds=90),
            },
        )
        return result.rowcount == 1


class Heartbeat:
    def __init__(self, engine: Engine, lease: Lease, clock: Callable[[], datetime]) -> None:
        self._engine, self._lease, self._clock = engine, lease, clock
        self._stop = threading.Event()
        self.lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(25):
            try:
                if renew(self._engine, self._lease, self._clock()):
                    continue
            except Exception:
                pass
            self.lost.set()
            return

    def __enter__(self) -> "Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=6)


def participants(conn: Connection, conversation: uuid.UUID) -> list[uuid.UUID]:
    return list(
        conn.execute(
            text(
                "select agent_id from public.conversation_participants "
                "where conversation_id=:id order by ordinal"
            ),
            {"id": conversation},
        ).scalars()
    )


def finish(
    conn: Connection,
    lease: Lease,
    status: str,
    client: LLMClient,
    now: datetime,
    *,
    retain_hold: bool = False,
    error: str | None = None,
) -> None:
    assert_lease(conn, lease, now)
    conn.execute(
        text(
            "update public.background_jobs set status=:status,lease_until=null,"
            "lease_token=null,last_error=:error where id=:id"
        ),
        {"status": status, "error": error, "id": lease.job},
    )
    conn.execute(
        text("update public.conversations set status=:status,ended_at=:now where id=:id"),
        {"status": status, "now": now, "id": lease.conversation},
    )
    if not retain_hold:
        for aid in participants(conn, lease.conversation):
            release_session(conn, aid, lease.conversation, client.price)
    conn.execute(
        text(
            "insert into public.conversation_events (conversation_id,kind) "
            "values (:id,:kind) on conflict do nothing"
        ),
        {"id": lease.conversation, "kind": status},
    )


def retry(conn: Connection, lease: Lease, client: LLMClient, now: datetime, delay: int) -> None:
    assert_lease(conn, lease, now)
    if lease.attempts >= 4:
        finish(conn, lease, "failed", client, now, error="retry_limit")
        return
    base = (30, 120, 600)[lease.attempts - 1]
    jitter = lease.job.int % 11
    conn.execute(
        text(
            "update public.background_jobs set status='retry_wait',run_at=:at,"
            "lease_until=null,lease_token=null,last_error='temporary_llm' where id=:id"
        ),
        {"id": lease.job, "at": now + timedelta(seconds=max(base, delay) + jitter)},
    )


def prepare(
    conn: Connection, lease: Lease, client: LLMClient, now: datetime, global_limit: Decimal
) -> tuple[uuid.UUID, int, ProfileInput, list[str]] | None:
    lock_interactions(conn)
    assert_lease(conn, lease, now)
    row = (
        conn.execute(
            text("select * from public.conversations where id=:id for update"),
            {"id": lease.conversation},
        )
        .mappings()
        .one()
    )
    ids = participants(conn, lease.conversation)
    if len(ids) != 2 or not pair_allowed(
        conn, (ids[0], ids[1]), now, conversation=lease.conversation
    ):
        finish(conn, lease, "cancelled", client, now, error="consent_lost")
        return None
    eligible = {r["id"]: r for r in eligible_agents(conn)}
    if any(
        row["profile_versions"].get(str(a)) != eligible[a]["approved_profile_version"] for a in ids
    ):
        finish(conn, lease, "cancelled", client, now, error="profile_changed")
        return None
    if row["started_at"] is None:
        conn.execute(
            text("update public.conversations set status='running',started_at=:now where id=:id"),
            {"id": lease.conversation, "now": now},
        )
    elif (now - row["started_at"]).total_seconds() >= 300:
        finish(conn, lease, "completed", client, now)
        return None
    turns = list(
        conn.execute(
            text("select content from public.messages where conversation_id=:id order by turn_no"),
            {"id": lease.conversation},
        ).scalars()
    )
    if len(turns) >= 8:
        finish(conn, lease, "completed", client, now)
        return None
    turn = len(turns) + 1
    aid = ids[(turn - 1) % 2]
    # Conservative bound for this implementation's max context/output bytes. A real adapter must
    # enforce provider token limits at the same boundary before being enabled.
    bound = client.price.cost(100000, 16000)
    if (
        any(exposure(conn, agent=a) > eligible[a]["daily_budget_usd"] for a in ids)
        or exposure(conn) > global_limit
    ):
        finish(conn, lease, "completed", client, now, error="budget_reduced")
        return None
    spent = Decimal(
        conn.execute(
            text(
                "select coalesce(sum(actual_usd),0) from public.usage_records "
                "where agent_id=:agent and conversation_id=:conv and kind='charge'"
            ),
            {"agent": aid, "conv": lease.conversation},
        ).scalar_one()
    )
    if (
        bound > held(conn, aid, lease.conversation)
        or spent + bound > eligible[aid]["max_session_usd"]
    ):
        finish(conn, lease, "completed", client, now, error="budget_limit")
        return None
    invocation = conn.execute(
        text(
            "select status from public.llm_invocations where conversation_id=:id and turn_no=:turn"
        ),
        {"id": lease.conversation, "turn": turn},
    ).scalar_one_or_none()
    if invocation in ("started", "unknown", "finished"):
        # Crash after dispatch/response may have incurred cost. Do not dispatch the same turn again.
        finish(conn, lease, "failed", client, now, retain_hold=True, error="unknown_llm_outcome")
        return None
    conn.execute(
        text(
            "insert into public.llm_invocations (conversation_id,turn_no,status) "
            "values (:id,:turn,'started') on conflict (conversation_id,turn_no) "
            "do update set status='started'"
        ),
        {"id": lease.conversation, "turn": turn},
    )
    profile = (
        conn.execute(
            text("select * from public.agent_profiles where agent_id=:id and version=:v"),
            {"id": aid, "v": row["profile_versions"][str(aid)]},
        )
        .mappings()
        .one()
    )
    public = ProfileInput.model_validate({k: profile[k] for k in ProfileInput.model_fields})
    return aid, turn, public, turns


def run_one(
    engine: Engine,
    client: LLMClient,
    moderator: Moderator,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    stopped: threading.Event | None = None,
    global_limit: Decimal = Decimal("10"),
) -> bool:
    stopped = stopped or threading.Event()
    if stopped.is_set():
        return False
    with engine.begin() as conn:
        lease = claim(conn, clock())
    if lease is None:
        return False
    try:
        while not stopped.is_set():
            with engine.begin() as conn:
                prepared = prepare(conn, lease, client, clock(), global_limit)
            if prepared is None:
                return True
            aid, turn, profile, turns = prepared
            if stopped.is_set():
                # No request was dispatched. Mark retryable before yielding the lease.
                with engine.begin() as conn:
                    lock_interactions(conn)
                    assert_lease(conn, lease, clock())
                    conn.execute(
                        text(
                            "update public.llm_invocations set status='retryable' "
                            "where conversation_id=:id and turn_no=:turn"
                        ),
                        {"id": lease.conversation, "turn": turn},
                    )
                    retry(conn, lease, client, clock(), 1)
                return True
            messages = build_prompt(profile, turns)
            try:
                with Heartbeat(engine, lease, clock) as heartbeat:
                    response = client.generate(messages)
                if heartbeat.lost.is_set():
                    raise LeaseLost()
            except TemporaryLLMError as exc:
                with engine.begin() as conn:
                    lock_interactions(conn)
                    assert_lease(conn, lease, clock())
                    conn.execute(
                        text(
                            "update public.llm_invocations set status='retryable' "
                            "where conversation_id=:id and turn_no=:turn"
                        ),
                        {"id": lease.conversation, "turn": turn},
                    )
                    retry(conn, lease, client, clock(), exc.retry_after)
                return True
            except (UnknownLLMOutcome, TimeoutError):
                with engine.begin() as conn:
                    lock_interactions(conn)
                    finish(
                        conn,
                        lease,
                        "failed",
                        client,
                        clock(),
                        retain_hold=True,
                        error="unknown_llm_outcome",
                    )
                return True
            cost = client.price.cost(response.input_tokens, response.output_tokens)
            value = None
            try:
                value = Utterance.model_validate_json(response.output)
                if not moderator.allowed(value.utterance) or not moderator.allowed(
                    value.reason or "終了"
                ):
                    raise UnsafeOutput()
            except (ValidationError, UnsafeOutput):
                value = None
            with engine.begin() as conn:
                lock_interactions(conn)
                assert_lease(conn, lease, clock())
                consume(
                    conn,
                    aid,
                    lease.conversation,
                    client.price,
                    f"{lease.conversation}:{turn}",
                    cost,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
                conn.execute(
                    text(
                        "update public.llm_invocations set status='finished' "
                        "where conversation_id=:id and turn_no=:turn"
                    ),
                    {"id": lease.conversation, "turn": turn},
                )
                conn.execute(
                    text("update public.conversations set cost_usd=cost_usd+:cost where id=:id"),
                    {"id": lease.conversation, "cost": cost},
                )
                if value is None:
                    finish(conn, lease, "failed", client, clock(), error="unsafe_output")
                    return True
                ids = participants(conn, lease.conversation)
                if stopped.is_set() or not pair_allowed(
                    conn, (ids[0], ids[1]), clock(), conversation=lease.conversation
                ):
                    finish(conn, lease, "cancelled", client, clock(), error="consent_lost")
                    return True
                conn.execute(
                    text(
                        "insert into public.messages "
                        "(conversation_id,agent_id,turn_no,content,moderation_status,"
                        "token_input,token_output) "
                        "values (:conv,:agent,:turn,:content,'allowed',:i,:o) "
                        "on conflict do nothing"
                    ),
                    {
                        "conv": lease.conversation,
                        "agent": aid,
                        "turn": turn,
                        "content": value.utterance,
                        "i": response.input_tokens,
                        "o": response.output_tokens,
                    },
                )
                if value.should_end or turn == 8:
                    finish(conn, lease, "completed", client, clock())
                    return True
        # A graceful stop between turns leaves no in-flight external call; resume from stored turns.
        with engine.begin() as conn:
            lock_interactions(conn)
            retry(conn, lease, client, clock(), 1)
        return True
    except LeaseLost:
        return True
    except Exception:
        # Never persist exception text, response body or credentials. Post-dispatch errors
        # conservatively retain reservations. The invocation marker prevents duplicate dispatch.
        with engine.begin() as conn:
            lock_interactions(conn)
            try:
                finish(
                    conn, lease, "failed", client, clock(), retain_hold=True, error="worker_error"
                )
            except LeaseLost:
                pass
        return True
