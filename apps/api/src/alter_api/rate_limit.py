"""API のレート制限（#32、docs/15）。

MVP の目安（docs/15）を、API 1 インスタンスのメモリ内で数える。

- 認証済みユーザーごと: 全リクエスト 60 回/分、書込（GET/HEAD/OPTIONS 以外）10 回/分
- 認証に失敗したリクエスト（token なし・不正）: 接続元ごとに 30 回/分。上限に達した接続元は、
  JWT の検証（JWKS の取得や Auth サーバーへの問い合わせ）の前に 429 で止める

上限を超えたら 429 ``RATE_LIMITED`` と ``Retry-After``（秒）を返す。人格提案の 3 回/日は
費用台帳と同じ DB で数える（suggestion_routes）ので、ここでは扱わない。

メモリ内の制限なので、API を再起動すると数え直しになり、複数のプロセス・台数では
共有されない。uvicorn を ``--workers 1`` で動かす前提で、複数台にする前に共有ストア型へ
置き換える（docs/15）。

接続元: 手元 PC の構成（ADR-015）では、公開 HTTPS の中継（Cloudflare Tunnel）が
loopback から API へつなぐため、``request.client`` は常に中継のアドレスになる。
``API_CLIENT_IP_HEADER``（例: ``CF-Connecting-IP``）を設定したときだけ、直接の接続元が
loopback の場合に限ってそのヘッダーを接続元として使う。未設定なら全員が 1 つの接続元として
数えられる（認証失敗の 30 回/分が全体で共有される）。
"""

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from ipaddress import ip_address

from fastapi import Request

from alter_api.errors import ApiError

WINDOW_SECONDS = 60.0
# 覚えておく鍵の上限（古いものから捨てる）。10 人の MVP では十分に大きい
MAX_KEYS = 10_000
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOOPBACK_UNKNOWN = "unknown"


class RateLimiter:
    """鍵ごとに直近 60 秒の回数を数える（スライディングウィンドウ）。スレッドセーフ。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        window: float = WINDOW_SECONDS,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self._clock = clock
        self._window = window
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> int | None:
        """1 回数える。上限を超えていれば数えずに ``Retry-After`` の秒数を返す。"""
        if limit <= 0:
            return None
        with self._lock:
            now = self._clock()
            hits = self._recent(key, now)
            if len(hits) >= limit:
                return self._retry_after(hits, now)
            hits.append(now)
            return None

    def blocked(self, key: str, limit: int) -> int | None:
        """数えずに、上限に達していれば ``Retry-After`` の秒数を返す。"""
        if limit <= 0:
            return None
        with self._lock:
            now = self._clock()
            hits = self._recent(key, now)
            return self._retry_after(hits, now) if len(hits) >= limit else None

    def _recent(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        return hits

    def _retry_after(self, hits: deque[float], now: float) -> int:
        return max(1, math.ceil(hits[0] + self._window - now))


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "RATE_LIMITED",
        "Too many requests",
        headers={"Retry-After": str(retry_after)},
    )


def check_user(request: Request, user_id: str) -> None:
    """認証済みユーザーの回数を数える。書込は別の上限でも数える。"""
    limiter: RateLimiter = request.app.state.rate_limiter
    settings = request.app.state.settings
    retry = limiter.hit(f"user:{user_id}", settings.api_rate_limit_per_minute)
    if retry is None and request.method not in _READ_METHODS:
        retry = limiter.hit(f"write:{user_id}", settings.api_write_rate_limit_per_minute)
    if retry is not None:
        raise rate_limited(retry)


def check_unauthenticated(request: Request, *, count: bool) -> None:
    """認証に失敗した接続元を数える（``count=False`` は確認だけ）。"""
    limiter: RateLimiter = request.app.state.rate_limiter
    settings = request.app.state.settings
    key = f"ip:{client_key(request, settings.api_client_ip_header)}"
    limit = settings.api_unauthenticated_rate_limit_per_minute
    retry = limiter.hit(key, limit) if count else limiter.blocked(key, limit)
    if retry is not None:
        raise rate_limited(retry)


def client_key(request: Request, header: str) -> str:
    peer = request.client.host if request.client else _LOOPBACK_UNKNOWN
    if header and _is_loopback(peer):
        forwarded = request.headers.get(header, "").strip()
        if forwarded and _valid_ip(forwarded):
            return forwarded
    return peer


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True
