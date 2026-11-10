"""Supabase access token の検証（docs/06-authentication-authorization.md「FastAPIでのJWT検証」）。

1. Bearer 形式と長さを確認する。
2. 非対称署名（ES256/RS256）は JWKS の ``kid`` 一致で署名検証する。鍵は短時間キャッシュし、
   未知の ``kid`` では一度だけ JWKS を再取得する（再取得の連発は間隔で抑える）。
3. ``iss`` / ``aud`` / ``exp`` / ``nbf`` / ``sub``(UUID) / ``role == authenticated`` を検査する。
   時刻の許容差は 30 秒以下。
4. 共有鍵 HS256 のプロジェクトでは JWKS が空になる。その場合だけ Auth サーバーの
   ``GET /auth/v1/user`` でトークンを確認する。JWKS に鍵がある状態の HS256 は拒否する。

トークンや JWKS の内容をログ・エラーメッセージに出さない。
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from alter_domain.settings import Settings

from alter_api.errors import ApiError

logger = logging.getLogger("alter_api.auth")

MAX_TOKEN_LENGTH = 8192
LEEWAY_SECONDS = 30
# 未知の kid による JWKS 再取得の最小間隔（ランダムな kid での連打対策）
MIN_JWKS_REFRESH_INTERVAL_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 5.0

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def unauthenticated(message: str = "Authentication required") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message, headers=_UNAUTHENTICATED_HEADERS)


def authorization_unavailable() -> ApiError:
    return ApiError(503, "AUTHORIZATION_UNAVAILABLE", "Authorization service is unavailable")


@dataclass(frozen=True)
class Principal:
    """検証済みの本人。``sub`` は Supabase Auth のユーザー ID（= profiles.id）。"""

    sub: uuid.UUID
    verified_by: Literal["jwks", "auth_server"]


class JwksCache:
    """JWKS を取得して kid ごとの鍵をキャッシュする。"""

    def __init__(
        self,
        url: str,
        http: httpx.Client,
        *,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._http = http
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    def _fetch(self) -> dict[str, jwt.PyJWK]:
        try:
            response = self._http.get(self._url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("JWKS fetch failed: %s", type(exc).__name__)
            raise authorization_unavailable() from exc
        raw_keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(raw_keys, list):
            logger.warning("JWKS response has no 'keys' list")
            raise authorization_unavailable()
        keys: dict[str, jwt.PyJWK] = {}
        for raw in raw_keys:
            if not isinstance(raw, dict) or not isinstance(raw.get("kid"), str) or not raw["kid"]:
                logger.warning("JWKS response contains an invalid key entry")
                raise authorization_unavailable()
            try:
                keys[raw["kid"]] = jwt.PyJWK(raw)
            except (jwt.PyJWTError, TypeError, ValueError) as exc:
                # 不正/未対応の鍵を空JWKSと見なし、HS256へ切り替えてはいけない。
                # ライブラリ例外の本文には鍵が入るため、型名だけをログに残す。
                logger.warning("JWKS key parsing failed: %s", type(exc).__name__)
                raise authorization_unavailable() from exc
        return keys

    def _refresh_locked(self) -> None:
        self._keys = self._fetch()
        self._fetched_at = self._clock()

    def _is_fresh(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at < self._ttl

    def keys(self) -> dict[str, jwt.PyJWK]:
        """有効期限内のキャッシュ、なければ取得した鍵一覧。"""
        with self._lock:
            if not self._is_fresh():
                self._refresh_locked()
            return dict(self._keys)

    def get(self, kid: str) -> jwt.PyJWK | None:
        with self._lock:
            if not self._is_fresh():
                self._refresh_locked()
            key = self._keys.get(kid)
            if key is not None:
                return key
            # 未知の kid: 鍵更新の可能性があるので一度だけ再取得する
            assert self._fetched_at is not None
            if self._clock() - self._fetched_at < MIN_JWKS_REFRESH_INTERVAL_SECONDS:
                return None
            self._refresh_locked()
            return self._keys.get(kid)


class JwtVerifier:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        base_url = settings.supabase_url.rstrip("/")
        self._issuer = settings.supabase_jwt_issuer
        self._audience = settings.supabase_jwt_audience
        self._algorithms = frozenset(settings.jwt_algorithm_list)
        self._publishable_key = settings.supabase_publishable_key
        self._user_url = f"{base_url}/auth/v1/user"
        self._http = http
        self._jwks = JwksCache(
            f"{base_url}/auth/v1/.well-known/jwks.json",
            http,
            ttl_seconds=settings.supabase_jwks_cache_seconds,
            clock=clock,
        )

    def verify(self, token: str) -> Principal:
        if not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 2:
            raise unauthenticated("Invalid access token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise unauthenticated("Invalid access token") from exc

        alg = header.get("alg")
        if isinstance(alg, str) and alg in self._algorithms:
            return self._verify_with_jwks(token, alg, header.get("kid"))
        if alg == "HS256":
            return self._verify_with_auth_server(token)
        raise unauthenticated("Invalid access token")

    # -- 非対称署名（JWKS） ---------------------------------------------------

    def _verify_with_jwks(self, token: str, alg: str, kid: object) -> Principal:
        if not isinstance(kid, str) or not kid:
            raise unauthenticated("Invalid access token")
        key = self._jwks.get(kid)
        # alg はヘッダー値ではなく鍵の種類と一致することを要求する（アルゴリズム混同の防止）
        if key is None or key.algorithm_name != alg:
            raise unauthenticated("Invalid access token")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise unauthenticated("Invalid access token") from exc
        return Principal(sub=self._check_subject(claims), verified_by="jwks")

    # -- 共有鍵 HS256（Auth サーバー照会） ------------------------------------

    def _verify_with_auth_server(self, token: str) -> Principal:
        # 非対称鍵が公開されているプロジェクトで HS256 を受け付けない
        if self._jwks.keys():
            raise unauthenticated("Invalid access token")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                # 署名は Auth サーバーが確認する。署名検証を外すと他の検査も既定で
                # 無効になるため、exp/nbf/iss/aud の検査を明示的に有効にする。
                options={
                    "verify_signature": False,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "require": ["exp", "iss", "aud", "sub"],
                },
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                algorithms=["HS256"],
            )
        except jwt.InvalidTokenError as exc:
            raise unauthenticated("Invalid access token") from exc
        expected_sub = self._check_subject(claims)

        try:
            response = self._http.get(
                self._user_url,
                headers={"Authorization": f"Bearer {token}", "apikey": self._publishable_key},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Auth user lookup failed: %s", type(exc).__name__)
            raise authorization_unavailable() from exc
        if response.status_code in (401, 403):
            raise unauthenticated("Invalid access token")
        if response.status_code != 200:
            logger.warning("Auth user lookup returned status %s", response.status_code)
            raise authorization_unavailable()
        try:
            user = response.json()
        except ValueError as exc:
            raise authorization_unavailable() from exc
        if (
            not isinstance(user, dict)
            or user.get("id") != str(expected_sub)
            or user.get("aud") != self._audience
            or user.get("role") != "authenticated"
        ):
            raise unauthenticated("Invalid access token")
        return Principal(sub=expected_sub, verified_by="auth_server")

    # -- 共通 -----------------------------------------------------------------

    @staticmethod
    def _check_subject(claims: dict[str, Any]) -> uuid.UUID:
        if claims.get("role") != "authenticated":
            raise unauthenticated("Invalid access token")
        sub = claims.get("sub")
        try:
            parsed = uuid.UUID(sub) if isinstance(sub, str) else None
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != sub:
            raise unauthenticated("Invalid access token")
        return parsed
