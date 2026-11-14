"""アカウント削除の受付と状況確認 API（#30 前半、#51 の preview 契約に合わせる）。

- ``POST /users/me/deletion``（``request_deletion``）: 5 分以内の OAuth 再認証が必要。202。
  受付済みなら同じ要求を 202 で返す（再送しても 2 件目を作らない）。
- ``GET /users/me/deletion``（``get_own_deletion``）: 本人 JWT だけで確認できる。削除要求後は
  共通認可（``BusinessConnection``）が 403 を返すため、招待の確認はしない。業務データは返さない。

どちらも通常の業務 API と同じく live モードでだけ DB を使う（Mock では 503）。
"""

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from alter_domain import account_deletion as domain
from alter_domain.auth_admin import AuthAdminUnavailable
from alter_domain.invitations import AccessDecision, InvitationDataError, evaluate_access
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from alter_api.auth import CurrentPrincipal
from alter_api.auth.jwt import Principal, authorization_unavailable
from alter_api.errors import ApiError, ErrorResponse

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}

router = APIRouter(tags=["account"])


class DeletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


class DeletionRequest(BaseModel):
    id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    requested_at: datetime
    completed_at: datetime | None = None

    @classmethod
    def from_record(cls, record: domain.DeletionRecord) -> "DeletionRequest":
        return cls(
            id=record.id,
            status=record.status,  # type: ignore[arg-type]
            requested_at=record.requested_at,
            completed_at=record.completed_at,
        )


def _require_live(request: Request) -> None:
    if request.app.state.settings.alter_mode != "live":
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Business persistence requires live mode")


def _deletion_connection(
    request: Request, principal: CurrentPrincipal
) -> Iterator[tuple[Connection, Principal, domain.DeletionRecord | None]]:
    """削除要求用の接続。招待とプロフィールを共通認可と同じ順で行ロックする。

    すでに削除要求済み（``deleted_at``）なら、招待の確認をせずに既存の要求を渡す（再送の冪等）。
    """
    _require_live(request)
    try:
        auth_user = request.app.state.auth_admin.get_user(principal.sub)
        with request.app.state.db_engine.begin() as conn:
            invitation = conn.execute(
                text("select id,status from public.invitations where auth_user_id=:id for update"),
                {"id": principal.sub},
            ).first()
            profile = conn.execute(
                text("select deleted_at from public.profiles where id=:id for update"),
                {"id": principal.sub},
            ).first()
            if profile is not None and profile.deleted_at is not None:
                yield conn, principal, domain.latest_request(conn, principal.sub)
                return
            if invitation is not None and invitation.status == "revoked":
                raise ApiError(403, "ACCOUNT_INACTIVE", "Account is inactive")
            if evaluate_access(conn, principal.sub, auth_user) != AccessDecision.ALLOWED:
                raise ApiError(403, "INVITATION_REQUIRED", "An active invitation is required")
            yield conn, principal, None
    except (AuthAdminUnavailable, InvitationDataError, SQLAlchemyError):
        raise authorization_unavailable() from None


DeletionContext = Annotated[
    tuple[Connection, Principal, domain.DeletionRecord | None],
    Depends(_deletion_connection, scope="function"),
]


def _status_connection(request: Request, principal: CurrentPrincipal) -> Iterator[Connection]:
    _require_live(request)
    try:
        with request.app.state.db_engine.connect() as conn:
            yield conn
    except SQLAlchemyError:
        raise authorization_unavailable() from None


StatusConnection = Annotated[Connection, Depends(_status_connection, scope="function")]


@router.post(
    "/users/me/deletion",
    status_code=202,
    response_model=DeletionRequest,
    responses=ERROR_RESPONSES,
    operation_id="request_deletion",
    summary="アカウント削除を要求する（5 分以内の再認証が必要。AI と配信はすぐ止まる）",
)
def request_deletion(
    body: DeletionInput,
    context: DeletionContext,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=200,
            description=(
                "再送の識別用（任意）。削除要求は利用者ごとに 1 件なので、キーがなくても重複しない"
            ),
        ),
    ] = None,
) -> DeletionRequest:
    del body, idempotency_key
    conn, principal, existing = context
    response.headers["Cache-Control"] = "no-store"
    if existing is not None:
        return DeletionRequest.from_record(existing)
    if not domain.recently_reauthenticated(principal.auth_methods, time.time()):
        raise ApiError(
            403,
            "REAUTHENTICATION_REQUIRED",
            "Sign in again within 5 minutes before requesting deletion",
        )
    return DeletionRequest.from_record(domain.request_deletion(conn, principal.sub))


@router.get(
    "/users/me/deletion",
    response_model=DeletionRequest,
    responses=ERROR_RESPONSES,
    operation_id="get_own_deletion",
    summary="自分の削除要求の状況（ID・状態・日時だけ）",
)
def get_own_deletion(
    principal: CurrentPrincipal, conn: StatusConnection, response: Response
) -> DeletionRequest:
    response.headers["Cache-Control"] = "no-store"
    record = domain.latest_request(conn, principal.sub)
    if record is None:
        raise ApiError(404, "NOT_FOUND", "No deletion request")
    return DeletionRequest.from_record(record)
