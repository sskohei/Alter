"""Unmounted #16/#30/#31 contract proposal, including private deletion progress."""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator


class Preferences(BaseModel):
    web_notifications: bool
    discord_dm_notifications: bool


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web_notifications: bool | None = None
    discord_dm_notifications: bool | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, f) is None for f in self.model_fields_set
        ):
            raise ValueError("At least one non-null preference is required")
        return self


class DeletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


class DeletionRequest(BaseModel):
    id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    requested_at: datetime
    completed_at: datetime | None = None


def register_account_contract(app: FastAPI, errors: dict[int | str, dict[str, Any]]) -> None:
    @app.get(
        "/api/v1/preferences",
        response_model=Preferences,
        responses=errors,
        operation_id="preview_get_preferences",
    )
    def preferences() -> Preferences:
        raise HTTPException(503, "Contract only")

    @app.patch(
        "/api/v1/preferences",
        response_model=Preferences,
        responses=errors,
        operation_id="preview_patch_preferences",
    )
    def patch_preferences(body: PreferencesPatch) -> Preferences:
        raise HTTPException(503, "Contract only")

    @app.post(
        "/api/v1/users/me/deletion",
        response_model=DeletionRequest,
        status_code=202,
        responses=errors,
        operation_id="preview_request_deletion",
    )
    def delete_account(body: DeletionInput) -> DeletionRequest:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/users/me/deletion",
        response_model=DeletionRequest,
        responses=errors,
        operation_id="preview_get_own_deletion",
    )
    def deletion_progress() -> DeletionRequest:
        raise HTTPException(503, "Contract only")
