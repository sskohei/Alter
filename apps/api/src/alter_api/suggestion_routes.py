"""Expose the optional suggestion contract; fail closed until a provider passes #14."""

from uuid import UUID

from alter_domain.agent_models import ProfileRevision
from alter_domain.agents import owned
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse

router = APIRouter(
    tags=["agents"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 429, 503)},
)


class SuggestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[str] = Field(min_length=1, max_length=20)


@router.post(
    "/agents/{agent_id}/profile/suggest",
    response_model=ProfileRevision,
    operation_id="suggest_agent_profile",
)
def suggest(
    agent_id: UUID, data: SuggestionInput, conn: BusinessConnection, principal: CurrentPrincipal
) -> ProfileRevision:
    owned(conn, principal.sub, agent_id)
    raise ApiError(503, "SERVICE_UNAVAILABLE", "LLM provider has not passed evaluation")
