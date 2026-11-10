"""Supabase Auth 管理 API の照会（招待の identity 照合用）。

ユーザーが編集できる ``user_metadata`` や ``identity_data`` のメール等は認可の根拠にしない。
認可に使うのは identity の ``provider`` と provider 側のユーザー ID（応答の ``id``）だけ。
秘密鍵・応答本文をログに出さない。
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from alter_domain.settings import Settings

logger = logging.getLogger("alter_domain.auth_admin")

HTTP_TIMEOUT_SECONDS = 5.0


class AuthAdminUnavailable(Exception):
    """Auth 管理 API に照会できない、または応答が不正（認可は 503 で止める）。"""


@dataclass(frozen=True)
class AuthIdentity:
    provider: str
    provider_user_id: str
    # 運営 CLI での人による本人照合の表示用。認可には使わない。
    display_hint: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    identities: tuple[AuthIdentity, ...]


class SupabaseAuthAdmin:
    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        self._base_url = settings.supabase_url.rstrip("/")
        self._secret_key = settings.supabase_secret_key
        self._http = http

    def get_user(self, user_id: uuid.UUID) -> AuthUser | None:
        """Auth ユーザーを取得する。存在しなければ None。"""
        if not self._secret_key:
            logger.warning("SUPABASE_SECRET_KEY is not configured")
            raise AuthAdminUnavailable("Auth admin key is not configured")
        try:
            response = self._http.get(
                f"{self._base_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": self._secret_key,
                    "Authorization": f"Bearer {self._secret_key}",
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Auth admin lookup failed: %s", type(exc).__name__)
            raise AuthAdminUnavailable("Auth admin lookup failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.warning("Auth admin lookup returned status %s", response.status_code)
            raise AuthAdminUnavailable("Auth admin lookup failed")
        try:
            data = response.json()
        except ValueError as exc:
            raise AuthAdminUnavailable("Auth admin response is not JSON") from exc
        return parse_auth_user(data, expected_id=user_id)


def parse_auth_user(data: Any, *, expected_id: uuid.UUID) -> AuthUser:
    if not isinstance(data, dict) or data.get("id") != str(expected_id):
        raise AuthAdminUnavailable("Auth admin response does not match the requested user")
    raw_identities = data.get("identities") or []
    if not isinstance(raw_identities, list):
        raise AuthAdminUnavailable("Auth admin response has invalid identities")
    identities: list[AuthIdentity] = []
    for raw in raw_identities:
        if not isinstance(raw, dict):
            raise AuthAdminUnavailable("Auth admin response has invalid identities")
        provider = raw.get("provider")
        provider_user_id = raw.get("id")
        owner = raw.get("user_id")
        if (
            not isinstance(provider, str)
            or not isinstance(provider_user_id, str)
            or not provider_user_id
            or (owner is not None and owner != str(expected_id))
        ):
            raise AuthAdminUnavailable("Auth admin response has invalid identities")
        identities.append(
            AuthIdentity(
                provider=provider,
                provider_user_id=provider_user_id,
                display_hint=_display_hint(raw.get("identity_data")),
            )
        )
    return AuthUser(id=expected_id, identities=tuple(identities))


def _display_hint(identity_data: Any) -> str | None:
    if not isinstance(identity_data, dict):
        return None
    for key in ("email", "user_name", "preferred_username", "name"):
        value = identity_data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
