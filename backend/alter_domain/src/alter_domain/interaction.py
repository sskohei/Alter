"""Shared consent and row-lock boundary for scheduling and every Worker turn."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, RowMapping, text

from alter_domain.budget import lock_budget


def lock_interactions(conn: Connection) -> None:
    # MVP <=10 owners: coarse, stable lock order also serializes with invitation revocation.
    lock_budget(conn)
    for table, key in (
        ("invitations", "id"),
        ("profiles", "id"),
        ("agents", "id"),
        ("agent_settings", "agent_id"),
    ):
        conn.execute(text(f"select {key} from public.{table} order by {key} for update")).all()


def eligible_agents(conn: Connection) -> list[RowMapping]:
    return list(
        conn.execute(
            text(
                "select a.id,a.owner_id,a.approved_profile_version,s.frequency_per_day,"
                "s.daily_budget_usd,"
                "s.max_session_usd,s.discord_publish,p.interests from public.agents a "
                "join public.profiles u on u.id=a.owner_id "
                "join public.invitations i on i.auth_user_id=u.id and i.status='active' "
                "join public.agent_settings s on s.agent_id=a.id "
                "join public.agent_profiles p on p.agent_id=a.id "
                "and p.version=a.approved_profile_version "
                "where a.status='active' and u.deleted_at is null and s.autonomy_enabled "
                "and s.frequency_per_day>0 and p.approved_at is not null "
                "and exists(select 1 from public.invitation_identities ii "
                "where ii.invitation_id=i.id) "
                "and not exists(select 1 from public.deletion_requests d "
                "where d.profile_id=u.id) "
                "and not exists(select 1 from public.llm_runs r where r.agent_id=a.id "
                "and r.status in ('reserved','generating','generated','moderating','unknown')) "
                "order by a.id"
            )
        ).mappings()
    )


def pair_allowed(
    conn: Connection, ids: tuple[UUID, UUID], now: datetime, *, conversation: UUID | None = None
) -> bool:
    if ids[0] == ids[1]:
        return False
    if conn.execute(
        text(
            "select 1 from public.agent_blocks where "
            "(blocker_agent_id=:a and blocked_agent_id=:b) or "
            "(blocker_agent_id=:b and blocked_agent_id=:a)"
        ),
        {"a": ids[0], "b": ids[1]},
    ).first():
        return False
    eligible = {row["id"]: row for row in eligible_agents(conn)}
    for aid in ids:
        if aid not in eligible:
            return False
        busy = conn.execute(
            text(
                "select 1 from public.conversation_participants p "
                "join public.conversations c on c.id=p.conversation_id where p.agent_id=:id "
                "and c.status in ('queued','running') "
                "and (cast(:conv as uuid) is null or c.id<>:conv)"
            ),
            {"id": aid, "conv": conversation},
        ).first()
        if busy:
            return False
        if conversation is None:
            count = conn.execute(
                text(
                    "select count(*) from public.conversation_participants p "
                    "join public.conversations c on c.id=p.conversation_id where p.agent_id=:id "
                    "and c.created_at >= date_trunc('day',"
                    "cast(:now as timestamptz) at time zone 'UTC') "
                    "at time zone 'UTC' "
                    "and c.created_at < (date_trunc('day', "
                    "cast(:now as timestamptz) at time zone 'UTC') + interval '1 day') "
                    "at time zone 'UTC'"
                ),
                {"id": aid, "now": now},
            ).scalar_one()
            if count >= eligible[aid]["frequency_per_day"]:
                return False
    return True
