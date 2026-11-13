"""共通エラー形式と request_id（docs/07-api-design.md）。

すべてのエラー応答は ``{"error": {"code", "message", "request_id"}}`` の形になる。
メッセージに秘密・トークン・入力本文を含めない。
"""

import logging
import uuid
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("alter_api")

REQUEST_ID_HEADER = "X-Request-ID"

ErrorCode = Literal[
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "INVITATION_REQUIRED",
    "ACCOUNT_INACTIVE",
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "CONFLICT",
    "VALIDATION_ERROR",
    "RATE_LIMITED",
    "AUTHORIZATION_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
]

_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """業務処理から投げる共通エラー。"""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


class RequestIdMiddleware:
    """リクエストごとに UUID の request_id を採番し、応答ヘッダーに付ける。

    クライアントが送った値は信用せず、常にサーバー側で採番する。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                name = REQUEST_ID_HEADER.lower().encode()
                if not any(key.lower() == name for key, _ in headers):
                    headers.append((name, request_id.encode()))
                if scope.get("path", "").startswith("/api/v1/"):
                    # Routers may already set no-store. Enforce one authoritative value.
                    headers = [
                        (key, value) for key, value in headers if key.lower() != b"cache-control"
                    ]
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class UnexpectedErrorMiddleware:
    """応答開始前の例外を安全な500に変換し、ASGIサーバーの詳細ログを防ぐ。

    CORSより内側に置くことで、500にも通常と同じCORSヘッダーを付ける。
    応答開始後は別の応答を送れないため、例外を再送出して接続を終了する。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except Exception as exc:
            if response_started:
                raise
            response = await _handle_unexpected_error(Request(scope), exc)
            await response(scope, receive, send)


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    # ミドルウェアより外側で起きたエラー用
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(request, exc.status_code, exc.code, exc.message, exc.headers)


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_TO_CODE.get(exc.status_code, "INTERNAL_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    if exc.status_code == 500:
        message = "Internal server error"
    return error_response(request, exc.status_code, code, message, dict(exc.headers or {}))


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    # 入力値そのものは応答に含めない（秘密や本文を反射しない）
    return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed")


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error request_id=%s type=%s",
        get_request_id(request),
        type(exc).__name__,
    )
    return error_response(request, 500, "INTERNAL_ERROR", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.add_middleware(UnexpectedErrorMiddleware)
