"""Review-only contracts for #7/#8; never mounted by alter_api.main.

Backend review must reconcile these with #7 before live acceptance.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from alter_api.errors import ErrorResponse
from fastapi import FastAPI, HTTPException
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
    display_name: str = Field(min_length=1, max_length=200)


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}


def create_contract_app() -> FastAPI:
    app = FastAPI(title="Alter frontend preview — NOT a live API", version="0.0.0-preview")

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

    return app
