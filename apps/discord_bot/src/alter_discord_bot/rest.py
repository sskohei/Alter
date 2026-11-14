"""投稿・DM 用の最小限の Discord REST 呼び出し（#28 レビュー対応）。

discord.py の HTTP 実装は、5xx や接続断のときに書き込みも内部で再送する。スレッド作成や
発言の送信が Discord 側では成功していて応答だけ失敗した場合、例外を受け取る前に二重に
作られてしまう。そこで投稿と DM の書き込みだけはこのクライアントで送り、内部で再送しない。

- 再送するのは 429（レート制限）だけ。Discord は 429 の要求を処理しないので重複しない。
  待ち時間は応答の ``retry_after`` / ``Retry-After`` に従う（固定値を持たない）
- 5xx・タイムアウト・接続断はそのまま例外にする（呼び出し側で ``needs_review`` にする）
- メンションは一切解決しない（``allowed_mentions.parse = []``）。URL の埋め込みも出さない
- 例外・ログに本文・トークンを含めない
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

API_BASE = "https://discord.com/api/v10"
TIMEOUT_SECONDS = 15
MAX_RATE_LIMIT_WAITS = 3
MAX_RATE_LIMIT_SECONDS = 60.0
# メッセージの flags: SUPPRESS_EMBEDS（URL のプレビューを出さない）
SUPPRESS_EMBEDS = 1 << 2
PUBLIC_THREAD = 11


class RestError(Exception):
    """Discord が応答したが失敗だった（``status`` と Discord のエラーコードだけを持つ）。"""

    def __init__(self, status: int, code: int) -> None:
        super().__init__(f"discord http {status} ({code})")
        self.status = status
        self.code = code


class DiscordRest:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_BASE,
        session: aiohttp.ClientSession | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._session = session
        self._sleep = sleep

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            )
        return self._session

    async def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "DiscordBot (https://github.com/sskohei/Alter, 0.1)",
        }
        for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
            async with self._client().request(
                method, self._base + path, json=body, headers=headers, allow_redirects=False
            ) as response:
                data = await _json(response)
                if response.status == 429 and attempt < MAX_RATE_LIMIT_WAITS:
                    await self._sleep(_retry_after(response, data))
                    continue
                if 200 <= response.status < 300:
                    return data
                code = data.get("code")
                raise RestError(response.status, code if isinstance(code, int) else 0)
        raise RestError(429, 0)  # pragma: no cover - ループの最後で必ず raise か return する

    async def create_thread(self, channel_id: str, name: str, archive_minutes: int) -> str:
        data = await self._request(
            "POST",
            f"/channels/{channel_id}/threads",
            {"name": name, "type": PUBLIC_THREAD, "auto_archive_duration": archive_minutes},
        )
        return str(data["id"])

    async def send_message(self, channel_id: str, content: str) -> str:
        data = await self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            {"content": content, "allowed_mentions": {"parse": []}, "flags": SUPPRESS_EMBEDS},
        )
        return str(data["id"])

    async def open_dm(self, user_id: str) -> str:
        # 同じ相手には同じ DM チャンネルが返る（何度呼んでも重複しない）
        data = await self._request("POST", "/users/@me/channels", {"recipient_id": user_id})
        return str(data["id"])


async def _json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(response: aiohttp.ClientResponse, data: dict[str, Any]) -> float:
    value: Any = data.get("retry_after", response.headers.get("Retry-After", 1))
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 1.0
    return min(max(seconds, 0.0), MAX_RATE_LIMIT_SECONDS)
