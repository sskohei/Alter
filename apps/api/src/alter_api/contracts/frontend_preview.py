"""Synthetic pagination contract only; never mounted by the production API."""

from typing import Annotated

from alter_api.users import ERROR_RESPONSES, Profile, router
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


class ProfilePage(BaseModel):
    items: list[Profile]
    next_cursor: str | None = None


def create_contract_app() -> FastAPI:
    app = FastAPI(title="Alter frontend preview — NOT a live API", version="0.0.0-preview")
    # Reuse #7's routes/models so user contracts cannot drift from the live API.
    # This app is exported as OpenAPI only, never served.
    app.include_router(router, prefix="/api/v1")

    @app.get(
        "/__mock/profiles",
        response_model=ProfilePage,
        responses=ERROR_RESPONSES,
        operation_id="preview_list_synthetic_profiles",
    )
    def list_profiles(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> ProfilePage:
        raise HTTPException(503, "Synthetic contract only")

    return app
