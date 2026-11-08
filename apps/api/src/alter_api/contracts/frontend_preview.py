"""Review-only contracts for #7/#8; never mounted by alter_api.main.

Backend review must reconcile these with #7 before live acceptance.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from alter_api.contracts.account_preview import register_account_contract
from alter_api.contracts.activity_preview import register_activity_contract
from alter_api.contracts.discord_preview import register_discord_contract
from alter_api.contracts.interaction_preview import register_interaction_contract
from alter_api.contracts.memory_preview import register_memory_contract
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    id: UUID
    display_name: str
    created_at: datetime
    web_notifications: bool
    discord_dm_notifications: bool
    onboarded_at: datetime | None = None


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=80)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: UUID


class ErrorResponse(BaseModel):
    error: ErrorBody


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


class ProfileRevision(PublicPersonality):
    version: int = Field(gt=0)
    approved_at: datetime | None


class AgentSettings(BaseModel):
    autonomy_enabled: bool
    frequency_per_day: int = Field(ge=0, le=2)
    daily_budget_usd: float = Field(ge=0, allow_inf_nan=False)
    max_session_usd: float = Field(ge=0, allow_inf_nan=False)
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


class AgentPatch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PrivateInfo(BaseModel):
    private_info: Text


class Approval(BaseModel):
    version: int = Field(gt=0)


class SuggestionInput(BaseModel):
    answers: dict[str, Text]


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}


def create_contract_app() -> FastAPI:
    app = FastAPI(
        title="Alter frontend preview — NOT a live API",
        version="0.0.0-preview",
        dependencies=[Depends(HTTPBearer())],
    )

    @app.get(
        "/api/v1/users/me",
        response_model=Profile,
        responses=ERRORS,
        operation_id="preview_get_current_user",
    )
    def get_user() -> Profile:
        raise HTTPException(503, "Contract only")

    @app.patch(
        "/api/v1/users/me",
        response_model=Profile,
        responses=ERRORS,
        operation_id="preview_patch_current_user",
    )
    def patch_user(body: ProfilePatch) -> Profile:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/agents/me",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_get_my_agent",
    )
    def get_agent() -> Agent:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/agents",
        response_model=Agent,
        status_code=201,
        responses=ERRORS,
        operation_id="preview_create_agent",
    )
    def create_agent(body: AgentDraftInput) -> Agent:
        raise HTTPException(503, "Contract only")

    @app.patch(
        "/api/v1/agents/{id}",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_patch_agent",
    )
    def patch_agent(id: UUID, body: AgentPatch) -> Agent:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/agents/{id}/profile",
        response_model=ProfileRevision,
        responses=ERRORS,
        operation_id="preview_get_personality",
    )
    def get_personality(id: UUID) -> ProfileRevision:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/agents/{id}/profile/private",
        response_model=PrivateInfo,
        responses=ERRORS,
        operation_id="preview_get_private_personality",
    )
    def get_private(id: UUID) -> PrivateInfo:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/agents/{id}/profile/revisions",
        response_model=ProfileRevision,
        responses=ERRORS,
        operation_id="preview_create_personality_revision",
    )
    def create_revision(id: UUID, body: AgentDraftInput) -> ProfileRevision:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/agents/{id}/profile/approve",
        response_model=Agent,
        responses=ERRORS,
        operation_id="preview_approve_personality",
    )
    def approve(id: UUID, body: Approval) -> Agent:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/agents/{id}/profile/suggest",
        response_model=ProfileRevision,
        responses=ERRORS,
        operation_id="preview_suggest_personality",
    )
    def suggest(id: UUID, body: SuggestionInput) -> ProfileRevision:
        raise HTTPException(503, "Contract only")

    register_discord_contract(app, ERRORS)
    register_account_contract(app, ERRORS)
    register_activity_contract(app, ERRORS)
    register_interaction_contract(app, ERRORS, AgentSettings)
    register_memory_contract(app, ERRORS)
    return app
