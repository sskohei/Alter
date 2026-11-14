"""Fenced, resumable conversation Worker. External calls happen outside DB transactions."""

import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from alter_domain.activity import notify_conversation_completed
from alter_domain.budget import (
    BudgetExceeded,
    UnsettledBudget,
    exposure,
    held,
    lock_budget,
    release_session,
    require_settled,
    session_exposure,
)
from alter_domain.interaction import eligible_agents, lock_interactions, pair_allowed
from alter_domain.llm import (
    StructuredLLMClient,
    TemporaryLLMError,
    UnknownLLMOutcome,
    UnsafeOutput,
)
from alter_domain.llm_execution import LLMExecution
from alter_domain.llm_selection import call_reservation
from alter_domain.sonnet import LLMUnavailable, SonnetClient


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
    # Conversations reference jobs, so deletion can leave a job without a conversation.
    row = conn.execute(
        text(
            "select j.id,j.attempts,c.id as conversation from public.background_jobs j "
            "join public.conversations c on c.job_id=j.id "
            "where j.kind='conversation.run' "
            "and ((j.status in ('queued','retry_wait') and j.run_at<=:now) "
            "or (j.status='running' and j.lease_until<=:now)) "
            "order by j.run_at,j.id for update of j skip locked limit 1"
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
    return Lease(row.id, token, row.conversation, row.attempts + 1)


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
    client: StructuredLLMClient,
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
    total = conn.execute(
        text(
            "select coalesce(sum(actual_usd),0) from public.usage_records "
            "where conversation_id=:id and kind='charge'"
        ),
        {"id": lease.conversation},
    ).scalar_one()
    conn.execute(
        text("update public.conversations set cost_usd=:cost where id=:id"),
        {"cost": total, "id": lease.conversation},
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

    if status == "completed":
        notify_conversation_completed(conn, lease.conversation)


def retry(
    conn: Connection, lease: Lease, client: StructuredLLMClient, now: datetime, delay: int
) -> None:
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


class WorkerStopped(Exception):
    pass


class ConsentLost(Exception):
    pass


class TimeLimit(Exception):
    pass


def check_context(
    conn: Connection,
    lease: Lease,
    now: datetime,
    global_limit: Decimal,
    *,
    active_run: uuid.UUID | None = None,
) -> None:
    """Called under the common lock before each external boundary and publication."""
    assert_lease(conn, lease, now)
    row = conn.execute(
        text(
            "select status,profile_versions,started_at from public.conversations "
            "where id=:id for update"
        ),
        {"id": lease.conversation},
    ).one()
    ids = participants(conn, lease.conversation)
    if (
        row.status not in ("queued", "running")
        or len(ids) != 2
        or not pair_allowed(
            conn,
            (ids[0], ids[1]),
            now,
            conversation=lease.conversation,
            active_run=active_run,
        )
    ):
        raise ConsentLost()
    eligible = {r["id"]: r for r in eligible_agents(conn, active_run=active_run)}
    if any(
        row.profile_versions.get(str(a)) != eligible[a]["approved_profile_version"] for a in ids
    ):
        raise ConsentLost()
    if row.started_at is not None and (now - row.started_at).total_seconds() >= 300:
        raise TimeLimit()
    if exposure(conn, now=now) > global_limit or any(
        exposure(conn, agent=a, now=now) > eligible[a]["daily_budget_usd"]
        or session_exposure(conn, a, lease.conversation) > eligible[a]["max_session_usd"]
        for a in ids
    ):
        raise BudgetExceeded()


def prepare(
    conn: Connection,
    lease: Lease,
    now: datetime,
    global_limit: Decimal,
) -> tuple[uuid.UUID, int, list[str]]:
    lock_interactions(conn)
    assert_lease(conn, lease, now)
    turns = list(
        conn.execute(
            text("select content from public.messages where conversation_id=:id order by turn_no"),
            {"id": lease.conversation},
        ).scalars()
    )
    turn = len(turns) + 1
    # Check crash markers before consent/budget: unknown requests must never release holds.
    invocation = conn.execute(
        text(
            "select status from public.llm_invocations where conversation_id=:id and turn_no=:turn"
        ),
        {"id": lease.conversation, "turn": turn},
    ).scalar_one_or_none()
    if invocation in ("started", "unknown", "finished"):
        raise UnknownLLMOutcome()
    check_context(conn, lease, now, global_limit)
    if turn > 8:
        raise TimeLimit()
    aid = participants(conn, lease.conversation)[(turn - 1) % 2]
    if held(conn, aid, lease.conversation) < call_reservation():
        raise BudgetExceeded()
    conn.execute(
        text(
            "update public.conversations set status='running',started_at=coalesce(started_at,:now) "
            "where id=:id"
        ),
        {"id": lease.conversation, "now": now},
    )
    conn.execute(
        text(
            "insert into public.llm_invocations (conversation_id,turn_no,status) "
            "values (:id,:turn,'started') on conflict (conversation_id,turn_no) "
            "do update set status='started'"
        ),
        {"id": lease.conversation, "turn": turn},
    )
    return aid, turn, turns


def settle_cancelled(conn: Connection, client: StructuredLLMClient) -> bool:
    """API/Bot cancellation precedes Worker claim; settle those untouched reservations too."""
    lock_interactions(conn)
    conversation = conn.execute(
        text(
            "select c.id from public.conversations c "
            "join public.background_jobs j on j.id=c.job_id "
            "where c.status='cancelled' and j.status='cancelled' "
            "and j.kind='conversation.run' and not exists "
            "(select 1 from public.conversation_events e where e.conversation_id=c.id "
            "and e.kind='cancelled') order by c.id for update of c skip locked limit 1"
        ),
    ).scalar_one_or_none()
    if conversation is None:
        return False
    unknown = (
        conn.execute(
            text(
                "select 1 from public.llm_invocations where conversation_id=:id "
                "and status in ('started','unknown') limit 1"
            ),
            {"id": conversation},
        ).first()
        is not None
    )
    try:
        require_settled(conn, conversation=conversation)
    except UnsettledBudget:
        unknown = True
    if not unknown:
        for aid in participants(conn, conversation):
            release_session(conn, aid, conversation, client.price)
    conn.execute(
        text(
            "insert into public.conversation_events (conversation_id,kind) "
            "values (:id,'cancelled') on conflict do nothing"
        ),
        {"id": conversation},
    )
    return True


def run_one(
    engine: Engine,
    client: StructuredLLMClient,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    stopped: threading.Event | None = None,
    global_limit: Decimal = Decimal("10"),
    forbidden: tuple[str, ...] = (),
) -> bool:
    stopped = stopped or threading.Event()
    if stopped.is_set():
        return False
    with engine.begin() as conn:
        if settle_cancelled(conn, client):
            return True
        lease = claim(conn, clock())
    if lease is None:
        return False

    execution: LLMExecution

    @contextmanager
    def authorized() -> Iterator[Connection]:
        with engine.begin() as conn:
            lock_interactions(conn)
            assert_lease(conn, lease, clock())
            if stopped.is_set():
                raise WorkerStopped()
            check_context(conn, lease, clock(), global_limit, active_run=execution.run_id)
            execution.validate_memory_context(conn)
            yield conn

    def before_request() -> None:
        with authorized():
            pass

    execution = LLMExecution(engine, client, authorized, global_limit, forbidden=forbidden)
    # Count-token retries inside the provider adapter must also recheck stop/consent/lease.
    previous_guard = None
    if isinstance(client, SonnetClient):
        previous_guard = client.before_request
        client.before_request = before_request
    try:
        with Heartbeat(engine, lease, clock):
            while True:
                execution.run_id = None
                execution.memory_snapshot = None
                with engine.begin() as conn:
                    aid, turn, turns = prepare(conn, lease, clock(), global_limit)
                value = execution.conversation_turn(aid, lease.conversation, turns)
                with engine.begin() as conn:
                    lock_interactions(conn)
                    assert_lease(conn, lease, clock())
                    if stopped.is_set():
                        raise WorkerStopped()
                    check_context(conn, lease, clock(), global_limit)
                    execution.validate_memory_context(conn)
                    usage = conn.execute(
                        text(
                            "select coalesce(sum(input_tokens),0) as i,"
                            "coalesce(sum(output_tokens),0) as o "
                            "from public.usage_records where kind='charge' "
                            "and starts_with(idempotency_key,:prefix)"
                        ),
                        {"prefix": f"llm:{execution.run_id}:"},
                    ).one()
                    conn.execute(
                        text(
                            "insert into public.messages "
                            "(conversation_id,agent_id,turn_no,content,moderation_status,"
                            "token_input,token_output) values "
                            "(:conv,:agent,:turn,:content,'allowed',:i,:o)"
                        ),
                        {
                            "conv": lease.conversation,
                            "agent": aid,
                            "turn": turn,
                            "content": value.utterance,
                            "i": usage.i,
                            "o": usage.o,
                        },
                    )
                    conn.execute(
                        text(
                            "update public.llm_invocations set status='finished' "
                            "where conversation_id=:id and turn_no=:turn"
                        ),
                        {"id": lease.conversation, "turn": turn},
                    )
                    conn.execute(
                        text(
                            "update public.conversations set cost_usd="
                            "(select coalesce(sum(actual_usd),0) from public.usage_records "
                            "where conversation_id=:id and kind='charge') where id=:id"
                        ),
                        {"id": lease.conversation},
                    )
                    if value.should_end or turn == 8:
                        finish(conn, lease, "completed", client, clock())
                        return True
    except LeaseLost:
        return True
    except TemporaryLLMError as exc:
        with engine.begin() as conn:
            lock_interactions(conn)
            try:
                assert_lease(conn, lease, clock())
                billed = conn.execute(
                    text(
                        "select 1 from public.usage_records where kind='charge' "
                        "and starts_with(idempotency_key,:prefix) limit 1"
                    ),
                    {"prefix": f"llm:{execution.run_id}:"},
                ).first()
                if billed:
                    # E.g. generation succeeded but moderation was rate-limited. Re-generating
                    # this turn would spend again; only wholly unbilled attempts can retry.
                    finish(conn, lease, "failed", client, clock(), error="partial_llm_failure")
                else:
                    conn.execute(
                        text(
                            "update public.llm_invocations set status='retryable' "
                            "where conversation_id=:id and status='started'"
                        ),
                        {"id": lease.conversation},
                    )
                    retry(conn, lease, client, clock(), exc.retry_after)
            except LeaseLost:
                pass
        return True
    except Exception as exc:
        if isinstance(exc, (WorkerStopped, ConsentLost)):
            status, error, retain = "cancelled", "consent_lost", False
        elif isinstance(exc, (BudgetExceeded, TimeLimit)):
            status, error, retain = (
                "completed",
                "budget_limit" if isinstance(exc, BudgetExceeded) else "time_limit",
                False,
            )
        elif isinstance(exc, UnsafeOutput):
            status, error, retain = "failed", "unsafe_output", False
        elif isinstance(exc, LLMUnavailable):
            status, error, retain = "failed", "llm_unavailable", False
        else:
            status, error, retain = "failed", "unknown_llm_outcome", True
        with engine.begin() as conn:
            lock_interactions(conn)
            try:
                # Other unsettled calls (e.g. crash before admission) always preserve holds.
                try:
                    require_settled(conn, conversation=lease.conversation)
                except UnsettledBudget:
                    retain = True
                finish(conn, lease, status, client, clock(), retain_hold=retain, error=error)
            except LeaseLost:
                pass
        return True
    finally:
        if isinstance(client, SonnetClient):
            client.before_request = previous_guard
