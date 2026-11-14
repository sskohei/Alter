"""Review-only Discord guild contract; settings use the live router."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel


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
    app: FastAPI | APIRouter, errors: dict[int | str, dict[str, Any]]
) -> None:
    @app.get(
        "/api/v1/discord/guilds",
        response_model=list[Guild],
        responses=errors,
        operation_id="preview_list_discord_guilds",
    )
    def guilds() -> list[Guild]:
        raise HTTPException(503, "Contract only")
