"""Only Discord remains a review contract; activity reuses the live router."""

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel


class DiscordConnection(BaseModel):
    connected: bool
    discord_user_id: str | None


def register_activity_contract(
    app: FastAPI | APIRouter, errors: dict[int | str, dict[str, Any]]
) -> None:
    @app.get(
        "/api/v1/discord/connection",
        response_model=DiscordConnection,
        responses=errors,
        operation_id="preview_get_discord_connection",
    )
    def discord_connection() -> DiscordConnection:
        raise HTTPException(503, "Contract only")
