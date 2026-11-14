"""リクエスト本文の大きさの上限（#32、docs/15）。

業務 API の本文は Pydantic で項目ごとに長さを制限しているが、検証の前に本文全体を
メモリへ読み込むため、極端に大きな本文で API を止められないよう、読み込みの段階で打ち切る。

- 既定 256 KiB。いちばん大きい正規の本文（AI の作成・改訂、全文字をエスケープした最悪値で
  約 134 KB）より大きい。``tests/test_api_body_limit.py`` が OpenAPI から最悪値を計算し、
  上限を超えないことを確かめる
- アイコンのアップロードは画像の上限（2 MiB、``alter_domain.icons.MAX_BYTES``）＋64 KiB。
  2 MiB を少し超える画像は、今までどおりルーターの画像の検証が 422 で返す（Web の BFF も同じ）

``Content-Length`` が上限を超えていれば読み込まずに 413、宣言がない（chunked）・宣言より
長い場合は、上限を超えた時点で読み込みをやめて 413 にする（ルーター側の応答は差し替える）。
応答は共通のエラー形式（``VALIDATION_ERROR``、本文の内容は含めない）。
"""

from collections.abc import Mapping

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from alter_api.errors import error_response

DEFAULT_MAX_BYTES = 256 * 1024
# アイコンは画像の上限（2 MiB）を少し超える分までルーターに渡し、画像の検証の 422 で返す
ICON_HEADROOM_BYTES = 64 * 1024
_NO_BODY_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        default: int = DEFAULT_MAX_BYTES,
        per_path: Mapping[str, int] | None = None,
    ) -> None:
        self.app = app
        self.default = default
        self.per_path = dict(per_path or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _NO_BODY_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self.per_path.get(scope["path"], self.default)
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _reject(scope, receive, send)
            return

        received = 0
        exceeded = False
        replaced = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            # 読み込みの途中で上限を超えたら、ルーター側がどんな応答を作っても 413 に差し替える
            # （FastAPI は本文の読み込み中の例外を 400 に変換するため）
            nonlocal replaced
            if not exceeded:
                await send(message)
                return
            if message["type"] == "http.response.start" and not replaced:
                replaced = True
                await _reject(scope, _empty_receive, send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            if not replaced:
                replaced = True
                await _reject(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    response = error_response(Request(scope), 413, "VALIDATION_ERROR", "Request body is too large")
    await response(scope, receive, send)
