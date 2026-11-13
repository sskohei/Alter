"""利用者プロフィールの共通ドメイン処理（docs/05「profiles」）。

``profiles.id`` は Supabase Auth の ``sub`` と同値。プロフィールの初回作成と、
招待・停止/削除の確認は API の共通認可（``alter_api.business.business_connection``）が行う。
ここの関数は、その認可を通ったトランザクションの中で呼ぶ。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, text

DISPLAY_NAME_MAX_LENGTH = 80

_COLUMNS = "id, display_name, web_notifications, discord_dm_notifications, onboarded_at, created_at"


@dataclass(frozen=True)
class ProfileRecord:
    id: uuid.UUID
    display_name: str
    web_notifications: bool
    discord_dm_notifications: bool
    onboarded_at: datetime | None
    created_at: datetime


class ProfileNotFound(Exception):
    """認可後にプロフィールが見つからない（同時削除など）。"""


def normalize_display_name(value: str) -> str:
    """前後の空白を除き、1〜80 文字で制御文字を含まない表示名にする。不正なら ValueError。"""
    name = value.strip()
    if not name:
        raise ValueError("display_name must not be blank")
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"display_name must be at most {DISPLAY_NAME_MAX_LENGTH} characters")
    if any(ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0 for ch in name):
        raise ValueError("display_name must not contain control characters")
    return name


def _record(row: Row[Any]) -> ProfileRecord:
    return ProfileRecord(
        id=row.id,
        display_name=row.display_name,
        web_notifications=row.web_notifications,
        discord_dm_notifications=row.discord_dm_notifications,
        onboarded_at=row.onboarded_at,
        created_at=row.created_at,
    )


def get_active_profile(conn: Connection, sub: uuid.UUID) -> ProfileRecord:
    row = conn.execute(
        text(f"select {_COLUMNS} from public.profiles where id = :sub and deleted_at is null"),
        {"sub": sub},
    ).first()
    if row is None:
        raise ProfileNotFound()
    return _record(row)


def update_display_name(conn: Connection, sub: uuid.UUID, display_name: str) -> ProfileRecord:
    name = normalize_display_name(display_name)
    row = conn.execute(
        text(
            f"update public.profiles set display_name = :name "
            f"where id = :sub and deleted_at is null returning {_COLUMNS}"
        ),
        {"sub": sub, "name": name},
    ).first()
    if row is None:
        raise ProfileNotFound()
    return _record(row)
