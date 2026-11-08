"""#26/#29 OAuth link contract proposal. Secret exchange stays in FastAPI."""

from datetime import datetime
from typing import Any

from alter_api.contracts.activity_preview import DiscordConnection
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field


class DiscordLinkStart(BaseModel):
    url: str
    state_expires_at: datetime


class DiscordLinkComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=16, max_length=256)


def register_discord_contract(app: FastAPI, errors: dict[int | str, dict[str, Any]]) -> None:
    @app.post(
        "/api/v1/discord/link/start",
        response_model=DiscordLinkStart,
        responses=errors,
        operation_id="preview_start_discord_link",
    )
    def start_link() -> DiscordLinkStart:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/discord/link/complete",
        response_model=DiscordConnection,
        responses=errors,
        operation_id="preview_complete_discord_link",
    )
    def complete_link(body: DiscordLinkComplete) -> DiscordConnection:
        raise HTTPException(503, "Contract only")

    @app.delete(
        "/api/v1/discord/connection",
        status_code=204,
        responses=errors,
        operation_id="preview_disconnect_discord",
    )
    def disconnect() -> Response:
        raise HTTPException(503, "Contract only")
