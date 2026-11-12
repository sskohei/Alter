import base64
import hashlib
import hmac
import json
from typing import Annotated
from uuid import UUID

from alter_domain import agents
from alter_domain.agent_models import (
    Agent,
    AgentDraftInput,
    AgentPatch,
    Approval,
    PrivateInfo,
    ProfileRevision,
    RevisionInput,
)
from alter_domain.profile_crypto import ProfileCipher
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse
from alter_api.idempotency import idempotency_key

router = APIRouter(
    tags=["agents"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
Key = Annotated[str | None, Depends(idempotency_key)]


def cipher_for(request: Request, needed: bool) -> ProfileCipher | None:
    if not needed:
        return None
    try:
        return ProfileCipher(
            request.app.state.settings.profile_encryption_key_v1.get_secret_value()
        )
    except ValueError:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Profile encryption is unavailable") from None


def request_digest(request: Request, data: AgentDraftInput, version: int) -> str:
    encoded = request.app.state.settings.api_idempotency_hmac_keys.get(version)
    try:
        secret = base64.b64decode(encoded.get_secret_value(), validate=True) if encoded else b""
        if len(secret) != 32:
            raise ValueError("Invalid key length")
    except ValueError:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Idempotency key is unavailable") from None
    return hmac.new(secret, data.model_dump_json().encode(), hashlib.sha256).hexdigest()


@router.get("/agents/me", response_model=Agent, operation_id="get_my_agent")
def get_my_agent(conn: BusinessConnection, principal: CurrentPrincipal) -> Agent:
    agent_id = conn.execute(
        text("select id from public.agents where owner_id=:id"), {"id": principal.sub}
    ).scalar_one_or_none()
    if agent_id is None:
        raise agents.AgentNotFound()
    return agents.owned(conn, principal.sub, agent_id)


@router.post("/agents", response_model=Agent, status_code=201, operation_id="create_agent")
def create_agent(
    data: AgentDraftInput,
    request: Request,
    conn: BusinessConnection,
    principal: CurrentPrincipal,
    key: Key,
) -> Agent:
    # Serialize on the owner before checking/replaying a successful creation.
    conn.execute(
        text("select id from public.profiles where id=:id for update"), {"id": principal.sub}
    )
    key_hash = hashlib.sha256(key.encode()).hexdigest() if key else None
    if key_hash:
        old = conn.execute(
            text(
                "select request_hash,response,hmac_key_version from public.api_idempotency "
                "where owner_id=:id and operation='agent.create' and key_hash=:key "
                "and created_at > now() - interval '24 hours'"
            ),
            {"id": principal.sub, "key": key_hash},
        ).first()
        if old:
            replay_digest = request_digest(request, data, old.hmac_key_version)
            if not hmac.compare_digest(old.request_hash, replay_digest):
                raise agents.AgentConflict()
            return Agent.model_validate(old.response)
    version = request.app.state.settings.api_idempotency_hmac_active_version
    digest = request_digest(request, data, version) if key_hash else None
    result = agents.create_agent(
        conn, principal.sub, data, cipher_for(request, bool(data.private_info))
    )
    if key_hash:
        conn.execute(
            text(
                "insert into public.api_idempotency "
                "(owner_id,operation,key_hash,request_hash,hmac_key_version,response) "
                "values (:id,'agent.create',:key,:digest,:version,cast(:response as jsonb))"
                " on conflict (owner_id,operation,key_hash) do update set "
                "request_hash=excluded.request_hash,hmac_key_version=excluded.hmac_key_version,"
                "response=excluded.response,created_at=now()"
            ),
            {
                "id": principal.sub,
                "key": key_hash,
                "digest": digest,
                "version": version,
                "response": json.dumps(result.model_dump(mode="json")),
            },
        )
    return result


@router.patch("/agents/{agent_id}", response_model=Agent, operation_id="update_agent")
def update_agent(
    agent_id: UUID, data: AgentPatch, conn: BusinessConnection, principal: CurrentPrincipal
) -> Agent:
    agents.owned(conn, principal.sub, agent_id, lock=True)
    conn.execute(
        text("update public.agents set name=:name where id=:id and owner_id=:owner"),
        {"id": agent_id, "owner": principal.sub, "name": data.name},
    )
    return agents.owned(conn, principal.sub, agent_id)


@router.get(
    "/agents/{agent_id}/profile", response_model=ProfileRevision, operation_id="get_agent_profile"
)
def get_profile(
    agent_id: UUID,
    conn: BusinessConnection,
    principal: CurrentPrincipal,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ProfileRevision:
    agents.owned(conn, principal.sub, agent_id)
    return agents.get_profile(conn, agent_id, version)


@router.post(
    "/agents/{agent_id}/profile/revisions",
    response_model=ProfileRevision,
    operation_id="create_profile_revision",
)
def revision(
    agent_id: UUID,
    data: RevisionInput,
    request: Request,
    conn: BusinessConnection,
    principal: CurrentPrincipal,
) -> ProfileRevision:
    agents.owned(conn, principal.sub, agent_id, lock=True)
    current = agents.latest(conn, agent_id)
    if current.version != data.expected_version:
        raise agents.AgentConflict()
    return agents.save_revision(
        conn, agent_id, current.version + 1, data, cipher_for(request, bool(data.private_info))
    )


@router.post(
    "/agents/{agent_id}/profile/approve", response_model=Agent, operation_id="approve_agent_profile"
)
def approve(
    agent_id: UUID, data: Approval, conn: BusinessConnection, principal: CurrentPrincipal
) -> Agent:
    return agents.approve(conn, principal.sub, agent_id, data.version)


@router.get(
    "/agents/{agent_id}/profile/private",
    response_model=PrivateInfo,
    operation_id="get_private_profile",
)
def private(
    agent_id: UUID, request: Request, conn: BusinessConnection, principal: CurrentPrincipal
) -> PrivateInfo:
    agents.owned(conn, principal.sub, agent_id)
    row = conn.execute(
        text(
            "select version,private_info_ciphertext,private_info_key_version "
            "from public.agent_profiles where agent_id=:id "
            "order by version desc limit 1"
        ),
        {"id": agent_id},
    ).one()
    value = ""
    if row.private_info_ciphertext is not None:
        cipher = cipher_for(request, True)
        assert cipher is not None
        if row.private_info_key_version != 1:
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Profile key version is unavailable")
        try:
            value = cipher.decrypt(bytes(row.private_info_ciphertext), agent_id, row.version)
        except Exception:
            raise ApiError(
                503, "SERVICE_UNAVAILABLE", "Profile decryption is unavailable"
            ) from None
    return PrivateInfo(private_info=value)
