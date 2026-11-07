"""#16/#24 settings/consent proposal, never mounted by the live API."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

Budget = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class AgentSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})
    autonomy_enabled: bool | None = None
    frequency_per_day: Annotated[int, Field(ge=0, le=2)] | None = None
    daily_budget_usd: Budget | None = None
    max_session_usd: Budget | None = None
    discord_publish: bool | None = None
    web_visibility: Literal["participants"] | None = None

    @model_validator(mode="after")
    def nonempty_nonnull(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, f) is None for f in self.model_fields_set
        ):
            raise ValueError("At least one non-null settings field is required")
        return self


class Guild(BaseModel):
    guild_id: str
    channel_id: str | None
    guild_enabled: bool
    consented_at: datetime | None
    revoked_at: datetime | None = None
    # Names are required by UI consent but absent from the published doc schema.
    # Nullable review fields: do not allow publishing when names are unavailable.
    guild_name: str | None = None
    channel_name: str | None = None


def register_interaction_contract(
    app: FastAPI, errors: dict[int | str, dict[str, Any]], settings_model: type[BaseModel]
) -> None:
    @app.get(
        "/api/v1/agents/{id}/settings",
        response_model=settings_model,
        responses=errors,
        operation_id="preview_get_interaction_settings",
    )
    def settings(id: UUID) -> BaseModel:
        raise HTTPException(503, "Contract only")

    @app.patch(
        "/api/v1/agents/{id}/settings",
        response_model=settings_model,
        responses=errors,
        operation_id="preview_patch_interaction_settings",
    )
    def patch_settings(id: UUID, body: AgentSettingsPatch) -> BaseModel:
        raise HTTPException(503, "Contract only")

    @app.put(
        "/api/v1/agents/{id}/blocks/{other_id}",
        status_code=204,
        responses=errors,
        operation_id="preview_block_agent",
    )
    def block(id: UUID, other_id: UUID) -> Response:
        raise HTTPException(503, "Contract only")

    @app.delete(
        "/api/v1/agents/{id}/blocks/{other_id}",
        status_code=204,
        responses=errors,
        operation_id="preview_unblock_agent",
    )
    def unblock(id: UUID, other_id: UUID) -> Response:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/discord/guilds",
        response_model=list[Guild],
        responses=errors,
        operation_id="preview_list_discord_guilds",
    )
    def guilds() -> list[Guild]:
        raise HTTPException(503, "Contract only")
