from typing import Literal

import uvicorn
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"
    mode: Literal["mock", "live"]


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title="Alter API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
        ],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", response_model=Health, operation_id="get_health", tags=["operations"])
    def health() -> Health:
        # Liveness only. This does not claim DB readiness or authentication support.
        return Health(mode=settings.alter_mode)

    return app


app = create_app()


def main() -> None:
    uvicorn.run("alter_api.main:app", host="127.0.0.1", port=8000, reload=True)
