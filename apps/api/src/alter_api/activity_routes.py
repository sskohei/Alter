"""会話一覧・詳細と Web 通知の API（#21、docs/07、#47 の preview 契約に合わせる）。

- ``GET /conversations``（``list_conversations``）
- ``GET /conversations/{id}``（``get_conversation``）
- ``GET /notifications``（``list_notifications``、``unread`` で未読/既読に絞る）
- ``PATCH /notifications/{id}/read``（``read_notification``、冪等）
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from alter_domain import activity, scheduling
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from alter_api.auth import CurrentPrincipal
from alter_api.business import BusinessConnection, InteractionConnection
from alter_api.errors import ApiError, ErrorResponse
from alter_api.idempotency import idempotency_key
from alter_api.pagination import Page, PageParams, page_params

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)
}

router = APIRouter(tags=["activity"])
Paging = Annotated[PageParams, Depends(page_params)]


class AgentReference(BaseModel):
    id: UUID
    name: str


class ConversationSummary(BaseModel):
    id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    partner: AgentReference
    started_at: datetime | None
    summary: str | None
    visibility: Literal["participants"]
    cost_usd: float | None = Field(
        default=None,
        ge=0,
        description=(
            "本人のAIのこの会話における確定費用（USD）。相手の費用は含まない。"
            "usage_records の kind='charge' の actual_usd の合計。"
            "queued/running の間は確定前として null、終了後は合計（課金なしは 0）。"
        ),
    )
    relationship_change: str | None = None

    @classmethod
    def from_record(cls, record: activity.ConversationRecord) -> "ConversationSummary":
        return cls(
            id=record.id,
            status=record.status,  # type: ignore[arg-type]
            partner=AgentReference(id=record.partner.id, name=record.partner.name),
            started_at=record.started_at,
            summary=record.summary,
            visibility="participants",
            cost_usd=None if record.cost_usd is None else float(record.cost_usd),
        )


class ConversationMessage(BaseModel):
    id: UUID
    agent_id: UUID
    turn_no: int = Field(ge=1, le=8)
    content: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    participants: list[AgentReference]
    messages: list[ConversationMessage]


class Notification(BaseModel):
    id: UUID
    kind: str = Field(description="現在は conversation.completed。未知の種類は汎用表示にする")
    body: str = Field(description="表示用の文字列。会話本文や相手の非公開情報は含めない")
    created_at: datetime
    read_at: datetime | None
    conversation_id: UUID | None = None

    @classmethod
    def from_record(cls, record: activity.NotificationRecord) -> "Notification":
        return cls(**record.__dict__)


def _not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} not found")


def _invalid_cursor() -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", "Invalid cursor")


class ManualInteractionOptions(BaseModel):
    enabled: bool
    unlimited_daily_count: bool


class StartedConversation(BaseModel):
    id: UUID


def _manual_enabled(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(
        settings.demo_manual_interactions_enabled
        and settings.alter_environment == "local"
        and settings.alter_mode == "live"
    )


@router.get(
    "/conversations/start",
    response_model=ManualInteractionOptions,
    responses=ERROR_RESPONSES,
    operation_id="get_manual_interaction_options",
)
def manual_interaction_options(
    request: Request, conn: BusinessConnection, response: Response
) -> ManualInteractionOptions:
    response.headers["Cache-Control"] = "no-store"
    enabled = _manual_enabled(request)
    return ManualInteractionOptions(enabled=enabled, unlimited_daily_count=enabled)


@router.post(
    "/conversations/start",
    response_model=StartedConversation,
    status_code=202,
    responses=ERROR_RESPONSES,
    operation_id="start_conversation",
    summary="ローカルデモで本人のAIの交流を1件予約する（日次回数制限なし）",
)
def start_conversation(
    request: Request,
    principal: CurrentPrincipal,
    conn: InteractionConnection,
    response: Response,
    key: Annotated[str | None, Depends(idempotency_key)],
) -> StartedConversation:
    response.headers["Cache-Control"] = "no-store"
    if not _manual_enabled(request):
        raise ApiError(403, "FORBIDDEN", "Manual interaction is disabled")
    if key is None:
        raise ApiError(422, "VALIDATION_ERROR", "Idempotency-Key is required")
    settings = request.app.state.settings
    if not settings.llm_enabled or not settings.anthropic_api_key.get_secret_value():
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Conversation generation is unavailable")
    job = scheduling.select_due(
        conn,
        datetime.now(UTC),
        settings.max_daily_cost_usd,
        owner_id=principal.sub,
        manual_key=key,
    )
    if job is None:
        raise ApiError(409, "CONFLICT", "No eligible partner or insufficient budget")
    conversation_id = conn.execute(
        text("select id from public.conversations where job_id=:job"), {"job": job}
    ).scalar_one()
    return StartedConversation(id=conversation_id)


@router.get(
    "/conversations",
    response_model=Page[ConversationSummary],
    responses=ERROR_RESPONSES,
    operation_id="list_conversations",
    summary="自分の AI が参加した会話の一覧（新しい順）",
)
def list_conversations(
    page: Paging, principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> Page[ConversationSummary]:
    response.headers["Cache-Control"] = "no-store"
    try:
        items, next_cursor = activity.list_conversations(
            conn, principal.sub, limit=page.limit, cursor=page.cursor
        )
    except activity.InvalidCursor:
        raise _invalid_cursor() from None
    return Page(items=[ConversationSummary.from_record(i) for i in items], next_cursor=next_cursor)


@router.get(
    "/conversations/{id}",
    response_model=ConversationDetail,
    responses=ERROR_RESPONSES,
    operation_id="get_conversation",
    summary="自分の AI が参加した会話の全文",
)
def get_conversation(
    id: UUID, principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> ConversationDetail:
    response.headers["Cache-Control"] = "no-store"
    try:
        detail = activity.get_conversation(conn, principal.sub, id)
    except activity.NotFound:
        raise _not_found("Conversation") from None
    summary = ConversationSummary.from_record(detail.conversation)
    return ConversationDetail(
        **summary.model_dump(),
        participants=[AgentReference(id=p.id, name=p.name) for p in detail.participants],
        messages=[ConversationMessage(**m.__dict__) for m in detail.messages],
    )


@router.get(
    "/notifications",
    response_model=Page[Notification],
    responses=ERROR_RESPONSES,
    operation_id="list_notifications",
    summary="自分への通知の一覧（新しい順）",
)
def list_notifications(
    page: Paging,
    principal: CurrentPrincipal,
    conn: BusinessConnection,
    response: Response,
    unread: Annotated[bool | None, Query(description="true で未読だけ、false で既読だけ")] = None,
) -> Page[Notification]:
    response.headers["Cache-Control"] = "no-store"
    try:
        items, next_cursor = activity.list_notifications(
            conn, principal.sub, limit=page.limit, cursor=page.cursor, unread=unread
        )
    except activity.InvalidCursor:
        raise _invalid_cursor() from None
    return Page(items=[Notification.from_record(i) for i in items], next_cursor=next_cursor)


@router.patch(
    "/notifications/{id}/read",
    response_model=Notification,
    responses=ERROR_RESPONSES,
    operation_id="read_notification",
    summary="通知を既読にする（再送しても最初の既読日時のまま）",
)
def read_notification(
    id: UUID, principal: CurrentPrincipal, conn: BusinessConnection, response: Response
) -> Notification:
    response.headers["Cache-Control"] = "no-store"
    try:
        record = activity.mark_notification_read(conn, principal.sub, id)
    except activity.NotFound:
        raise _not_found("Notification") from None
    return Notification.from_record(record)
