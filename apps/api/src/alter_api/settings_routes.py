"""交流設定・ブロック・通知設定 API（#16、docs/07、#48・#51 の preview 契約に合わせる）。

- ``GET/PATCH /agents/{id}/settings``（``get/patch_interaction_settings``）
- ``PUT/DELETE /agents/{id}/blocks/{other_id}``（``block_agent`` / ``unblock_agent``、204・冪等）
- ``GET/PATCH /preferences``（``get_preferences`` / ``patch_preferences``）
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from alter_domain import interaction_settings as domain
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}

router = APIRouter(tags=["settings"])

Budget = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]

_CONFLICT_MESSAGES = {
    "agent_not_active": "The agent must be approved and active",
    "consent_terms_unavailable": "Interaction consent terms are not available yet",
}


class AgentSettings(BaseModel):
    autonomy_enabled: bool
    frequency_per_day: int = Field(ge=0, le=2)
    daily_budget_usd: float = Field(ge=0, allow_inf_nan=False)
    max_session_usd: float = Field(ge=0, allow_inf_nan=False)
    web_visibility: Literal["participants"]
    discord_publish: bool
    updated_at: datetime

    @classmethod
    def from_record(cls, record: domain.AgentSettingsRecord) -> "AgentSettings":
        return cls(
            autonomy_enabled=record.autonomy_enabled,
            frequency_per_day=record.frequency_per_day,
            daily_budget_usd=float(record.daily_budget_usd),
            max_session_usd=float(record.max_session_usd),
            web_visibility="participants",
            discord_publish=record.discord_publish,
            updated_at=record.updated_at,
        )


class AgentSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})
    # 文字列の "yes" や "1" を真偽値・整数として受け付けない
    autonomy_enabled: StrictBool | None = None
    frequency_per_day: Annotated[StrictInt, Field(ge=0, le=2)] | None = None
    daily_budget_usd: Budget | None = None
    max_session_usd: Budget | None = None
    discord_publish: StrictBool | None = None
    web_visibility: Literal["participants"] | None = None

    @model_validator(mode="after")
    def nonempty_nonnull(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, f) is None for f in self.model_fields_set
        ):
            raise ValueError("At least one non-null settings field is required")
        return self

    def changes(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in self.model_fields_set:
            value = getattr(self, field)
            # JSON の数値を表記どおりの 10 進数として扱い、小数 6 桁を超える値は 422 にする
            values[field] = Decimal(str(value)) if field.endswith("_usd") else value
        return values


class Preferences(BaseModel):
    web_notifications: bool
    discord_dm_notifications: bool


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})
    web_notifications: StrictBool | None = None
    discord_dm_notifications: StrictBool | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, f) is None for f in self.model_fields_set
        ):
            raise ValueError("At least one non-null preference is required")
        return self


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, domain.AgentNotFound):
        return ApiError(404, "NOT_FOUND", "Agent not found")
    if isinstance(exc, domain.SettingsConflict):
        message = _CONFLICT_MESSAGES.get(exc.reason, "Settings cannot be changed now")
        return ApiError(409, "CONFLICT", message)
    return ApiError(422, "VALIDATION_ERROR", "Request validation failed")


_DOMAIN_ERRORS = (domain.AgentNotFound, domain.SettingsConflict, domain.SettingsInvalid)


@router.get(
    "/agents/{id}/settings",
    response_model=AgentSettings,
    responses=ERROR_RESPONSES,
    operation_id="get_interaction_settings",
    summary="自分の AI の交流設定を取得する",
)
def get_interaction_settings(
    id: UUID, principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> AgentSettings:
    response.headers["Cache-Control"] = "no-store"
    try:
        return AgentSettings.from_record(domain.get_settings(conn, principal.sub, id))
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc) from None


@router.patch(
    "/agents/{id}/settings",
    response_model=AgentSettings,
    responses=ERROR_RESPONSES,
    operation_id="patch_interaction_settings",
    summary="自律交流・頻度・予算・Discord 公開の設定を変更する",
)
def patch_interaction_settings(
    id: UUID,
    body: AgentSettingsPatch,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    request: Request,
    response: Response,
) -> AgentSettings:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    try:
        record = domain.update_settings(
            conn,
            principal.sub,
            id,
            body.changes(),
            max_daily_budget_usd=settings.agent_daily_budget_max_usd,
            consent_version=settings.alter_consent_version,
        )
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc) from None
    return AgentSettings.from_record(record)


@router.put(
    "/agents/{id}/blocks/{other_id}",
    status_code=204,
    responses=ERROR_RESPONSES,
    operation_id="block_agent",
    summary="相手の AI をブロックする（再送しても同じ結果）",
)
def block_agent(
    id: UUID, other_id: UUID, principal: CurrentPrincipal, conn: BusinessConnection
) -> Response:
    try:
        domain.block_agent(conn, principal.sub, id, other_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc) from None
    return Response(status_code=204)


@router.delete(
    "/agents/{id}/blocks/{other_id}",
    status_code=204,
    responses=ERROR_RESPONSES,
    operation_id="unblock_agent",
    summary="ブロックを解除する（ブロックしていなくても 204）",
)
def unblock_agent(
    id: UUID, other_id: UUID, principal: CurrentPrincipal, conn: BusinessConnection
) -> Response:
    try:
        domain.unblock_agent(conn, principal.sub, id, other_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc) from None
    return Response(status_code=204)


@router.get(
    "/preferences",
    response_model=Preferences,
    responses=ERROR_RESPONSES,
    operation_id="get_preferences",
    summary="通知設定を取得する",
)
def get_preferences(
    principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> Preferences:
    response.headers["Cache-Control"] = "no-store"
    record = domain.get_preferences(conn, principal.sub)
    return Preferences(**record.__dict__)


@router.patch(
    "/preferences",
    response_model=Preferences,
    responses=ERROR_RESPONSES,
    operation_id="patch_preferences",
    summary="通知設定を変更する",
)
def patch_preferences(
    body: PreferencesPatch,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    response: Response,
) -> Preferences:
    response.headers["Cache-Control"] = "no-store"
    changes = {f: getattr(body, f) for f in body.model_fields_set}
    record = domain.update_preferences(conn, principal.sub, changes)
    return Preferences(**record.__dict__)
