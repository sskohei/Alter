"""Discord OAuth2（authorization code）で本人の Discord ユーザー ID だけを取得する（#26）。

- スコープは ``identify`` だけ。ギルド・メール等は求めない
- アクセストークンは ID の取得にだけ使い、保存・ログ出力しない。取得後は失効を試みて破棄する
- 応答本文・コード・トークン・クライアントシークレットをログや例外メッセージに含めない
"""

import base64
import logging
from urllib.parse import urlencode

import httpx

from alter_domain.settings import Settings

logger = logging.getLogger("alter_domain.discord_oauth")

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
API_BASE = "https://discord.com/api/v10"
HTTP_TIMEOUT_SECONDS = 5.0
SCOPE = "identify"


class DiscordOAuthNotConfigured(Exception):
    """クライアント ID・シークレット・リダイレクト先のどれかが未設定（API は 503）。"""


class DiscordOAuthUnavailable(Exception):
    """Discord に到達できない、または応答が想定外（API は 503）。"""


class DiscordCodeRejected(Exception):
    """Discord が認可コードを受け付けなかった（期限切れ・使用済み・別の redirect_uri など）。"""


def _is_snowflake(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and value.isdigit() and 15 <= len(value) <= 21


class DiscordOAuthClient:
    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        self._client_id = settings.discord_client_id
        self._client_secret = settings.discord_client_secret.get_secret_value()
        self._redirect_uri = settings.discord_redirect_uri
        self._http = http

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._redirect_uri)

    def _require_configured(self) -> None:
        if not self.configured:
            raise DiscordOAuthNotConfigured("Discord OAuth is not configured")

    def authorize_url(self, state: str) -> str:
        self._require_configured()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "scope": SCOPE,
                "state": state,
                "redirect_uri": self._redirect_uri,
                "prompt": "consent",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _basic_auth(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def fetch_user_id(self, code: str) -> str:
        """認可コードを交換し、Discord ユーザー ID を返す。トークンは返さない。"""
        self._require_configured()
        try:
            token_response = self._http.post(
                f"{API_BASE}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
                headers={"Authorization": self._basic_auth()},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Discord token exchange failed: %s", type(exc).__name__)
            raise DiscordOAuthUnavailable("Discord token exchange failed") from None
        if token_response.status_code in (400, 401):
            raise DiscordCodeRejected("Discord rejected the authorization code")
        if token_response.status_code != 200:
            logger.warning("Discord token exchange returned status %s", token_response.status_code)
            raise DiscordOAuthUnavailable("Discord token exchange failed")
        try:
            token_data = token_response.json()
        except ValueError:
            raise DiscordOAuthUnavailable("Discord token response is not JSON") from None
        access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
        scopes = str(token_data.get("scope", "")).split() if isinstance(token_data, dict) else []
        if not isinstance(access_token, str) or not access_token:
            raise DiscordOAuthUnavailable("Discord token response is invalid")
        try:
            if SCOPE not in scopes:
                raise DiscordOAuthUnavailable("Discord token response is invalid")
            return self._user_id(access_token)
        finally:
            self._revoke(access_token)

    def _user_id(self, access_token: str) -> str:
        try:
            response = self._http.get(
                f"{API_BASE}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Discord user lookup failed: %s", type(exc).__name__)
            raise DiscordOAuthUnavailable("Discord user lookup failed") from None
        if response.status_code != 200:
            logger.warning("Discord user lookup returned status %s", response.status_code)
            raise DiscordOAuthUnavailable("Discord user lookup failed")
        try:
            data = response.json()
        except ValueError:
            raise DiscordOAuthUnavailable("Discord user response is not JSON") from None
        user_id = data.get("id") if isinstance(data, dict) else None
        if not _is_snowflake(user_id):
            raise DiscordOAuthUnavailable("Discord user response is invalid")
        assert isinstance(user_id, str)
        return user_id

    def _revoke(self, access_token: str) -> None:
        """取得したトークンを失効させる（失敗しても連携は続ける。保存はしていない）。"""
        try:
            response = self._http.post(
                f"{API_BASE}/oauth2/token/revoke",
                data={"token": access_token, "token_type_hint": "access_token"},
                headers={"Authorization": self._basic_auth()},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                logger.info("Discord token revoke returned status %s", response.status_code)
        except httpx.HTTPError as exc:
            logger.info("Discord token revoke failed: %s", type(exc).__name__)
