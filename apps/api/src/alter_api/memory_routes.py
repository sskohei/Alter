import base64
import hashlib
import uuid
from datetime import datetime
from typing import Annotated

from alter_domain import agents, memory
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse
from alter_api.idempotency import idempotency_key
from alter_api.pagination import Page, PageParams, page_params

router = APIRouter(
    tags=["memory"], responses={c: {"model": ErrorResponse} for c in (401, 403, 404, 409, 422, 503)}
)
Paging = Annotated[PageParams, Depends(page_params)]
Key = Annotated[str | None, Depends(idempotency_key)]


def encode_cursor(created: datetime, identifier: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created.isoformat()}|{identifier}".encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True).decode()
        timestamp, identifier = decoded.split("|")
        date = datetime.fromisoformat(timestamp)
        if date.tzinfo is None:
            raise ValueError()
        return date, uuid.UUID(identifier)
    except (ValueError, UnicodeError):
        raise ApiError(422, "VALIDATION_ERROR", "Invalid cursor") from None


@router.get("/memories", response_model=Page[memory.Memory], operation_id="list_memories")
def list_memories(
    agent_id: uuid.UUID, page: Paging, conn: BusinessConnection, principal: CurrentPrincipal
) -> Page[memory.Memory]:
    agents.owned(conn, principal.sub, agent_id)
    cursor_clause = ""
    params: dict[str, object] = {"agent": agent_id, "limit": page.limit + 1}
    if page.cursor:
        params["created"], params["id"] = decode_cursor(page.cursor)
        cursor_clause = " and (m.created_at,m.id)<(:created,:id) "
    rows = (
        conn.execute(
            text(
                "select m.* from public.memories m "
                "join public.conversations c on c.id=m.source_conversation_id and "
                "c.status='completed' "
                "where m.agent_id=:agent and m.status='active' and not exists "
                "(select 1 from public.memory_source_suppressions s "
                "where s.agent_id=m.agent_id and "
                "s.source_conversation_id=m.source_conversation_id) "
                + cursor_clause
                + "order by m.created_at desc,m.id desc limit :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [memory.Memory.model_validate(dict(row)) for row in rows[: page.limit]]
    cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page.limit else None
    return Page(items=items, next_cursor=cursor)


@router.delete("/memories/{memory_id}", status_code=204, operation_id="delete_memory")
def delete_memory(
    memory_id: uuid.UUID, conn: BusinessConnection, principal: CurrentPrincipal, key: Key
) -> Response:
    key_hash = hashlib.sha256(key.encode()).hexdigest() if key else None
    if key_hash:
        old = conn.execute(
            text(
                "select request_hash from public.api_idempotency "
                "where owner_id=:owner and operation='memory.delete' and key_hash=:key"
            ),
            {"owner": principal.sub, "key": key_hash},
        ).scalar_one_or_none()
        if old is not None:
            if old != str(memory_id):
                raise agents.AgentConflict()
            return Response(status_code=204)
    memory.forget(conn, principal.sub, memory_id)
    if key_hash:
        conn.execute(
            text(
                "insert into public.api_idempotency "
                "(owner_id,operation,key_hash,request_hash,response) "
                "values (:owner,'memory.delete',:key,:id,'{}')"
            ),
            {"owner": principal.sub, "key": key_hash, "id": str(memory_id)},
        )
    return Response(status_code=204)


@router.get(
    "/relationships", response_model=Page[memory.Relationship], operation_id="list_relationships"
)
def list_relationships(
    page: Paging, conn: BusinessConnection, principal: CurrentPrincipal
) -> Page[memory.Relationship]:
    params: dict[str, object] = {"owner": principal.sub, "limit": page.limit + 1}
    clause = ""
    if page.cursor:
        try:
            params["after"] = uuid.UUID(
                base64.b64decode(page.cursor, altchars=b"-_", validate=True).decode()
            )
        except (ValueError, UnicodeError):
            raise ApiError(422, "VALIDATION_ERROR", "Invalid cursor") from None
        clause = " and other.id>:after "
    rows = (
        conn.execute(
            text(
                "select other.id as other_agent_id,other.name as other_agent_name,"
                "r.conversation_count,r.shared_interests,r.last_met_at,exists "
                "(select 1 from public.agent_blocks b where "
                "(b.blocker_agent_id=a.id and b.blocked_agent_id=other.id) or "
                "(b.blocker_agent_id=other.id and b.blocked_agent_id=a.id)) as blocked "
                "from public.agents a join public.agent_relationships r "
                "on (r.agent_low_id=a.id or r.agent_high_id=a.id) "
                "join public.agents other on other.id=case when r.agent_low_id=a.id "
                "then r.agent_high_id else r.agent_low_id end "
                "join public.profiles u on u.id=other.owner_id "
                "where a.owner_id=:owner and a.status<>'deleted' and other.status<>'deleted' "
                "and u.deleted_at is null " + clause + "order by other.id limit :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [memory.Relationship.model_validate(dict(row)) for row in rows[: page.limit]]
    cursor = (
        base64.urlsafe_b64encode(str(items[-1].other_agent_id).encode()).decode()
        if len(rows) > page.limit
        else None
    )
    return Page(items=items, next_cursor=cursor)
