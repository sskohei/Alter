"""Discord 本人連携 API（#26、docs/06・docs/07、#52 の preview 契約に合わせる）。

- ``GET /discord/connection``（``get_discord_connection``）
- ``DELETE /discord/connection``（``disconnect_discord``、204・冪等）
- ``POST /discord/link/start``（``start_discord_link``）
- ``POST /discord/link/complete``（``complete_discord_link``、Web の callback から呼ぶ）
- ``GET /discord/guilds``（``list_discord_guilds``）

MVP では Web からギルドの投稿先を設定する API は公開しない（Bot の ``/server setup`` を使う）。
"""

from datetime import datetime
from typing import Annotated, Any

from alter_domain import discord_link as domain
from alter_domain.discord_oauth import (
    DiscordOAuthClient,
    DiscordOAuthNotConfigured,
    DiscordOAuthUnavailable,
)
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse, error_response

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}
COMPLETE_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "state またはコードが無効（やり直し）"},
    **ERROR_RESPONSES,
}

router = APIRouter(prefix="/discord", tags=["discord"])


def discord_oauth(request: Request) -> DiscordOAuthClient:
    client: DiscordOAuthClient = request.app.state.discord_oauth
    return client


OAuth = Annotated[DiscordOAuthClient, Depends(discord_oauth)]


class DiscordConnection(BaseModel):
    connected: bool
    discord_user_id: str | None

    @classmethod
    def from_record(cls, record: domain.ConnectionRecord) -> "DiscordConnection":
        return cls(connected=record.connected, discord_user_id=record.discord_user_id)


class DiscordLinkStart(BaseModel):
    url: str
    state_expires_at: datetime


class DiscordLinkComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=16, max_length=256)


class Guild(BaseModel):
    guild_id: str
    guild_name: str | None = None
    channel_id: str | None
    channel_name: str | None = None
    guild_enabled: bool
    consented_at: datetime | None
    revoked_at: datetime | None = None


def _unavailable() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", "Discord linking is not available")


_OUTCOME_ERRORS: dict[domain.LinkOutcome, tuple[int, Any, str]] = {
    domain.LinkOutcome.STATE_INVALID: (400, "OAUTH_STATE_INVALID", "Invalid or expired state"),
    domain.LinkOutcome.CODE_REJECTED: (
        400,
        "OAUTH_STATE_INVALID",
        "Discord authorization was not accepted",
    ),
    domain.LinkOutcome.ALREADY_LINKED: (409, "CONFLICT", "Discord is already linked"),
    domain.LinkOutcome.DISCORD_ACCOUNT_IN_USE: (
        409,
        "CONFLICT",
        "This Discord account is linked to another user",
    ),
}


@router.get(
    "/connection",
    response_model=DiscordConnection,
    responses=ERROR_RESPONSES,
    operation_id="get_discord_connection",
    summary="Discord 連携の状態（トークンは返さない）",
)
def get_discord_connection(
    principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> DiscordConnection:
    response.headers["Cache-Control"] = "no-store"
    return DiscordConnection.from_record(domain.get_connection(conn, principal.sub))


@router.delete(
    "/connection",
    status_code=204,
    responses=ERROR_RESPONSES,
    operation_id="disconnect_discord",
    summary="Discord 連携を解除する（未送信の DM・投稿とギルド別同意も取り消す。冪等）",
)
def disconnect_discord(principal: CurrentPrincipal, conn: BusinessConnection) -> Response:
    domain.disconnect(conn, principal.sub)
    return Response(status_code=204)


@router.post(
    "/link/start",
    response_model=DiscordLinkStart,
    responses=ERROR_RESPONSES,
    operation_id="start_discord_link",
    summary="連携を開始する（10 分・使い捨ての state と Discord の認可 URL）",
)
def start_discord_link(
    principal: CurrentPrincipal, conn: BusinessConnection, oauth: OAuth, response: Response
) -> DiscordLinkStart:
    response.headers["Cache-Control"] = "no-store"
    try:
        started = domain.start_link(conn, principal.sub, oauth)
    except domain.AlreadyLinked:
        raise ApiError(409, "CONFLICT", "Discord is already linked") from None
    except DiscordOAuthNotConfigured:
        raise _unavailable() from None
    return DiscordLinkStart(url=started.url, state_expires_at=started.state_expires_at)


@router.post(
    "/link/complete",
    response_model=DiscordConnection,
    responses=COMPLETE_ERROR_RESPONSES,
    operation_id="complete_discord_link",
    summary="Discord の認可コードで連携を完了する（state は本人・10 分以内・1 回だけ）",
)
def complete_discord_link(
    body: DiscordLinkComplete,
    request: Request,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    oauth: OAuth,
) -> DiscordConnection | JSONResponse:
    try:
        outcome, record = domain.complete_link(
            conn, principal.sub, code=body.code, state=body.state, oauth=oauth
        )
    except (DiscordOAuthNotConfigured, DiscordOAuthUnavailable):
        raise _unavailable() from None
    if outcome is not domain.LinkOutcome.LINKED or record is None:
        # 例外にせず応答する: state の消費と監査をコミットし、同じ state を再利用させない
        status, code, message = _OUTCOME_ERRORS[outcome]
        error = error_response(request, status, code, message)
        error.headers["Cache-Control"] = "no-store"
        return error
    result = DiscordConnection.from_record(record)
    return JSONResponse(result.model_dump(), headers={"Cache-Control": "no-store"})


@router.get(
    "/guilds",
    response_model=list[Guild],
    responses=ERROR_RESPONSES,
    operation_id="list_discord_guilds",
    summary="本人の AI がギルド別公開に同意したギルドと投稿先",
)
def list_discord_guilds(
    principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> list[Guild]:
    response.headers["Cache-Control"] = "no-store"
    return [Guild(**g.__dict__) for g in domain.list_guilds(conn, principal.sub)]
