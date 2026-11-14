"""Review-only contracts; never mounted by alter_api.main.

User and activity routes reuse the live contract; personality routes remain preview-only.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from alter_api.activity_routes import router as activity_router
from alter_api.contracts.activity_preview import register_activity_contract
from alter_api.errors import ErrorResponse
from alter_api.users import Profile, router
from alter_domain.agent_models import AgentPatch
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(max_length=2000)]
Items = Annotated[list[Text], Field(max_length=20)]


class PublicPersonality(BaseModel):
    personality: Text
    values_text: Text
    interests: Items
    speaking_style: Text
    interaction_policy: Text
    shareable_info: Text
    restrictions: Text
    hobbies: Items = Field(default_factory=list)
    strengths: Items = Field(default_factory=list)
    weaknesses: Items = Field(default_factory=list)


class AgentDraftInput(PublicPersonality):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    private_info: Text = ""
    icon_path: str | None = None


class RevisionInput(AgentDraftInput):
    expected_version: int = Field(ge=1)


class ProfileRevision(PublicPersonality):
    version: int = Field(gt=0)
    approved_at: datetime | None


class AgentSettings(BaseModel):
    autonomy_enabled: bool
    frequency_per_day: int
    daily_budget_usd: float
    max_session_usd: float
    web_visibility: Literal["participants"]
    discord_publish: bool
    updated_at: datetime


class Agent(BaseModel):
    id: UUID
    name: str
    status: Literal["draft", "active", "paused", "deleted"]
    approved_profile_version: int | None
    created_at: datetime
    settings: AgentSettings | None = None
    icon_url: str | None = None


class PrivateInfo(BaseModel):
    private_info: Text


class Approval(BaseModel):
    version: int = Field(gt=0)


class SuggestionInput(BaseModel):
    answers: dict[str, Text]


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}


class ProfilePage(BaseModel):
    items: list[Profile]
    next_cursor: str | None = None


def create_contract_app() -> FastAPI:
    app = FastAPI(
        title="Alter frontend preview — NOT a live API",
        version="0.0.0-preview",
    )

    app.include_router(router, prefix="/api/v1")
    app.include_router(activity_router, prefix="/api/v1")
    preview_router = APIRouter(dependencies=[Depends(HTTPBearer())])

    @preview_router.get(
        "/__mock/profiles",
        response_model=ProfilePage,
        responses=ERRORS,
        operation_id="preview_list_synthetic_profiles",
    )
    def list_profiles(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> ProfilePage:
        raise HTTPException(503, "Synthetic contract only")

    @preview_router.get(
        "/api/v1/agents/me",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_get_my_agent",
    )
    def get_agent() -> Agent:
        raise HTTPException(503, "Contract only")

    @preview_router.post(
        "/api/v1/agents",
        response_model=Agent,
        status_code=201,
        responses=ERRORS,
        operation_id="preview_create_agent",
    )
    def create_agent(body: AgentDraftInput) -> Agent:
        raise HTTPException(503, "Contract only")

    @preview_router.patch(
        "/api/v1/agents/{id}",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_patch_agent",
    )
    def patch_agent(id: UUID, body: AgentPatch) -> Agent:
        raise HTTPException(503, "Contract only")

    @preview_router.get(
        "/api/v1/agents/{id}/profile",
        response_model=ProfileRevision,
        responses=ERRORS,
        operation_id="preview_get_personality",
    )
    def get_personality(
        id: UUID, version: Annotated[int | None, Query(ge=1)] = None
    ) -> ProfileRevision:
        raise HTTPException(503, "Contract only")

    @preview_router.get(
        "/api/v1/agents/{id}/profile/private",
        response_model=PrivateInfo,
        responses=ERRORS,
        operation_id="preview_get_private_personality",
    )
    def get_private(id: UUID) -> PrivateInfo:
        raise HTTPException(503, "Contract only")

    @preview_router.post(
        "/api/v1/agents/{id}/profile/revisions",
        response_model=ProfileRevision,
        responses=ERRORS,
        operation_id="preview_create_personality_revision",
    )
    def create_revision(id: UUID, body: RevisionInput) -> ProfileRevision:
        raise HTTPException(503, "Contract only")

    @preview_router.post(
        "/api/v1/agents/{id}/profile/approve",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_approve_personality",
    )
    def approve(id: UUID, body: Approval) -> Agent:
        raise HTTPException(503, "Contract only")

    @preview_router.post(
        "/api/v1/agents/{id}/profile/suggest",
        response_model=PublicPersonality,
        responses=ERRORS,
        operation_id="preview_suggest_personality",
    )
    def suggest(id: UUID, body: SuggestionInput) -> PublicPersonality:
        raise HTTPException(503, "Contract only")

    register_activity_contract(preview_router, ERRORS)
    app.include_router(preview_router)
    return app
