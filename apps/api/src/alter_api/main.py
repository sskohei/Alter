from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
import uvicorn
from alter_domain import agents
from alter_domain.auth_admin import SupabaseAuthAdmin
from alter_domain.db import create_db_engine
from alter_domain.icons import IconStorage, IconStorageUnavailable, InvalidIcon
from alter_domain.settings import Settings
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from alter_api.activity_routes import router as activity_router
from alter_api.agent_routes import router as agent_router
from alter_api.auth import JwtVerifier
from alter_api.deletion_routes import router as deletion_router
from alter_api.errors import (
    REQUEST_ID_HEADER,
    ErrorResponse,
    RequestIdMiddleware,
    install_error_handlers,
)
from alter_api.idempotency import IDEMPOTENCY_KEY_HEADER
from alter_api.settings_routes import router as settings_router
from alter_api.suggestion_routes import router as suggestion_router
from alter_api.users import router as users_router

API_V1_PREFIX = "/api/v1"


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"
    mode: Literal["mock", "live"]


def create_api_v1_router() -> APIRouter:
    """業務 API のルーター。各 Issue のエンドポイントはここへ追加する。"""
    router = APIRouter(prefix=API_V1_PREFIX)
    router.include_router(users_router)
    router.include_router(settings_router)
    router.include_router(agent_router)
    router.include_router(suggestion_router)
    router.include_router(activity_router)
    router.include_router(deletion_router)
    return router


def _add_shared_components(app: FastAPI) -> None:
    """共通エラー形式と Bearer 認証を OpenAPI に常に載せ、生成 TypeScript から使えるようにする。"""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = FastAPI.openapi(app)
        _, definitions = models_json_schema(
            [(ErrorResponse, "serialization")], ref_template="#/components/schemas/{model}"
        )
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {}).update(definitions.get("$defs", {}))
        components.setdefault("securitySchemes", {})["SupabaseBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Supabase Auth の access token（refresh token は送らない）",
        }
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or Settings()
    owns_client = http_client is None
    client = http_client or httpx.Client(follow_redirects=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        app.state.db_engine.dispose()
        if owns_client:
            client.close()

    # 本番は Swagger UI / ReDoc を閉じる。/openapi.json はスモーク確認と型生成のため公開する
    # （API の形だけで秘密は含まない）。
    docs_enabled = settings.api_docs_enabled
    app = FastAPI(
        title="Alter API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.db_engine = create_db_engine(settings)
    app.state.auth_admin = SupabaseAuthAdmin(settings, client)
    app.state.icon_storage = IconStorage(settings, client)
    app.state.jwt_verifier = JwtVerifier(settings, client)

    install_error_handlers(app)

    async def agent_not_found(request: Any, exc: Exception) -> Any:
        from alter_api.errors import error_response

        return error_response(request, 404, "NOT_FOUND", "Agent not found")

    async def agent_conflict(request: Any, exc: Exception) -> Any:
        from alter_api.errors import error_response

        return error_response(request, 409, "CONFLICT", "Agent version or creation conflict")

    async def invalid_icon(request: Any, exc: Exception) -> Any:
        from alter_api.errors import error_response

        return error_response(request, 422, "VALIDATION_ERROR", "Invalid icon")

    async def unavailable_icon(request: Any, exc: Exception) -> Any:
        from alter_api.errors import error_response

        return error_response(request, 503, "SERVICE_UNAVAILABLE", "Icon storage is unavailable")

    app.add_exception_handler(InvalidIcon, invalid_icon)
    app.add_exception_handler(IconStorageUnavailable, unavailable_icon)
    app.add_exception_handler(agents.AgentNotFound, agent_not_found)
    app.add_exception_handler(agents.AgentConflict, agent_conflict)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", IDEMPOTENCY_KEY_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # 外側から request_id → CORS → 予期しない例外の変換 → ルーター。
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", response_model=Health, operation_id="get_health", tags=["operations"])
    def health() -> Health:
        # Liveness only. This does not claim DB readiness or authentication support.
        return Health(mode=settings.alter_mode)

    app.include_router(create_api_v1_router())
    _add_shared_components(app)
    return app


app = create_app()


def main() -> None:
    uvicorn.run("alter_api.main:app", host="127.0.0.1", port=8000, reload=True)
