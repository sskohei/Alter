"""Owned memory browsing/deletion and current relationship counts (#25, PR #62 contract)."""

import base64
from typing import Annotated, Any
from uuid import UUID

from alter_domain import agents, memory_read
from alter_domain.activity import _VISIBLE_CONVERSATION, InvalidCursor, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from alter_api.activity_routes import _invalid_cursor
from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ErrorResponse
from alter_api.pagination import Page, PageParams, page_params

router = APIRouter(
    tags=["memory"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)},
)
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("/memories", response_model=Page[memory_read.Memory], operation_id="list_memories")
def list_memories(
    agent_id: UUID,
    page: Paging,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    response: Response,
) -> Page[memory_read.Memory]:
    agents.owned(conn, principal.sub, agent_id)
    response.headers["Cache-Control"] = "no-store"
    params: dict[str, Any] = {"agent": agent_id, "limit": page.limit + 1}
    after = ""
    if page.cursor:
        try:
            params["created"], params["after"] = decode_cursor(page.cursor)
        except InvalidCursor:
            raise _invalid_cursor() from None
        after = " and (m.created_at,m.id)<(:created,:after) "
    rows = (
        conn.execute(
            text(
                "select m.* from public.memories m left join public.conversations c "
                "on c.id=m.source_conversation_id where m.agent_id=:agent and m.status='active' "
                "and m.created_at>now()-interval '180 days' "
                "and not exists(select 1 from public.memory_tombstones t "
                "where t.agent_id=m.agent_id and t.dedupe_key=m.dedupe_key) "
                "and not exists(select 1 from public.memory_source_suppressions s "
                "where s.agent_id=m.agent_id and "
                "s.source_conversation_id=m.source_conversation_id) "
                "and (m.source_conversation_id is null or (c.status='completed' and "
                + _VISIBLE_CONVERSATION
                + " and exists(select 1 from public.conversation_participants p "
                "where p.conversation_id=c.id and p.agent_id=m.agent_id))) "
                + after
                + "order by m.created_at desc,m.id desc limit :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [memory_read.Memory.model_validate(dict(row)) for row in rows[: page.limit]]
    cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page.limit else None
    return Page(items=items, next_cursor=cursor)


@router.delete("/memories/{id}", status_code=204, operation_id="delete_memory")
def delete_memory(id: UUID, principal: CurrentPrincipal, conn: BusinessConnection) -> Response:
    memory_read.forget(conn, principal.sub, id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get(
    "/relationships",
    response_model=Page[memory_read.Relationship],
    operation_id="list_relationships",
)
def list_relationships(
    page: Paging,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    response: Response,
) -> Page[memory_read.Relationship]:
    response.headers["Cache-Control"] = "no-store"
    params: dict[str, Any] = {"owner": principal.sub, "limit": page.limit + 1}
    after = ""
    if page.cursor:
        try:
            params["after"] = UUID(
                base64.b64decode(page.cursor, altchars=b"-_", validate=True).decode()
            )
        except (ValueError, UnicodeError):
            raise _invalid_cursor() from None
        after = " and other.id>:after "
    rows = (
        conn.execute(
            text(
                "with mine as (select * from public.agents where owner_id=:owner and "
                "status<>'deleted'), "
                "pairs as (select case when r.agent_low_id=a.id then r.agent_high_id else "
                "r.agent_low_id end as id "
                "from mine a join public.agent_relationships r on a.id in "
                "(r.agent_low_id,r.agent_high_id) "
                "union select q.agent_id from mine a join public.conversation_participants "
                "p on p.agent_id=a.id "
                "join public.conversation_participants q on "
                "q.conversation_id=p.conversation_id and q.agent_id<>a.id) "
                "select other.id as other_agent_id,other.name as other_agent_name,counts.n "
                "as conversation_count, "
                "counts.last_met as last_met_at,exists(select 1 from public.agent_blocks b "
                "where b.blocker_agent_id=a.id and b.blocked_agent_id=other.id) as blocked, "
                "array(select distinct i from unnest(mp.interests) i where i=any(op.interests) "
                "order by i limit 20) as shared_interests "
                "from mine a cross join pairs join public.agents other on other.id=pairs.id "
                "join public.profiles u on u.id=other.owner_id "
                "left join public.agent_profiles mp on mp.agent_id=a.id and "
                "mp.version=a.approved_profile_version "
                "left join public.agent_profiles op on op.agent_id=other.id and "
                "op.version=other.approved_profile_version "
                "left join public.agent_relationships r on r.agent_low_id=least(a.id,other.id) "
                "and r.agent_high_id=greatest(a.id,other.id) "
                "cross join lateral(select count(*)::int as n,max(c.ended_at) as last_met "
                "from public.conversations c join public.conversation_participants p on "
                "p.conversation_id=c.id "
                "and p.agent_id=a.id join public.conversation_participants q on "
                "q.conversation_id=c.id "
                "and q.agent_id=other.id where c.status='completed' and "
                + _VISIBLE_CONVERSATION
                + ") counts "
                "where other.status<>'deleted' and u.deleted_at is null "
                "and (counts.n>0 or r.conversation_count=0) "
                + after
                + "order by other.id limit :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [memory_read.Relationship.model_validate(dict(row)) for row in rows[: page.limit]]
    cursor = (
        base64.urlsafe_b64encode(str(items[-1].other_agent_id).encode()).decode()
        if len(rows) > page.limit
        else None
    )
    return Page(items=items, next_cursor=cursor)
