"""``GET/PATCH /api/v1/users/me``（docs/07、#44 の preview 契約に合わせる）。

招待・停止/削除の確認とプロフィールの初回作成は共通認可 ``BusinessConnection`` が行う。
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from alter_domain import profiles
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection
from alter_api.errors import ApiError, ErrorResponse

# 生成クライアントの型をそろえるため、共通エラーをすべて載せる（#44 の preview と同じ）
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}

router = APIRouter(prefix="/users", tags=["users"])


class Profile(BaseModel):
    id: uuid.UUID
    display_name: str
    created_at: datetime
    web_notifications: bool
    discord_dm_notifications: bool
    onboarded_at: datetime | None = None

    @classmethod
    def from_record(cls, record: profiles.ProfileRecord) -> "Profile":
        return cls(
            id=record.id,
            display_name=record.display_name,
            created_at=record.created_at,
            web_notifications=record.web_notifications,
            discord_dm_notifications=record.discord_dm_notifications,
            onboarded_at=record.onboarded_at,
        )


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=profiles.DISPLAY_NAME_MAX_LENGTH,
            description="前後の空白を除いて 1〜80 文字。空白だけ・制御文字は 422",
        ),
    ]

    @field_validator("display_name")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return profiles.normalize_display_name(value)


def _account_inactive() -> ApiError:
    return ApiError(403, "ACCOUNT_INACTIVE", "Account is inactive")


@router.get(
    "/me",
    response_model=Profile,
    responses=ERROR_RESPONSES,
    operation_id="get_current_user",
    summary="招待・有効状態を確認して自分のプロフィールを作成/取得する",
)
def get_current_user(
    principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> Profile:
    response.headers["Cache-Control"] = "no-store"
    try:
        return Profile.from_record(profiles.get_active_profile(conn, principal.sub))
    except profiles.ProfileNotFound as exc:
        raise _account_inactive() from exc


@router.patch(
    "/me",
    response_model=Profile,
    responses=ERROR_RESPONSES,
    operation_id="patch_current_user",
    summary="自分の表示名を変更する",
)
def patch_current_user(
    body: ProfilePatch, principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> Profile:
    response.headers["Cache-Control"] = "no-store"
    try:
        record = profiles.update_display_name(conn, principal.sub, body.display_name)
    except profiles.ProfileNotFound as exc:
        raise _account_inactive() from exc
    return Profile.from_record(record)
