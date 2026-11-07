"""Unmounted #21/#23 contract proposal. Backend review is still required."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

Limit = Annotated[int, Query(ge=1, le=50)]


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
    cost_usd: float | None = Field(default=None, ge=0)
    relationship_change: str | None = None


class ConversationMessage(BaseModel):
    id: UUID
    agent_id: UUID
    turn_no: int = Field(ge=1, le=8)
    content: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    participants: list[AgentReference]
    messages: list[ConversationMessage]


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None


class Notification(BaseModel):
    id: UUID
    kind: str
    body: str
    created_at: datetime
    read_at: datetime | None
    conversation_id: UUID | None = None


class NotificationPage(BaseModel):
    items: list[Notification]
    next_cursor: str | None


class DiscordConnection(BaseModel):
    connected: bool
    discord_user_id: str | None


def register_activity_contract(app: FastAPI, errors: dict[int | str, dict[str, Any]]) -> None:
    @app.get(
        "/api/v1/conversations",
        response_model=ConversationPage,
        responses=errors,
        operation_id="preview_list_conversations",
    )
    def conversations(cursor: str | None = None, limit: Limit = 20) -> ConversationPage:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/conversations/{id}",
        response_model=ConversationDetail,
        responses=errors,
        operation_id="preview_get_conversation",
    )
    def conversation(id: UUID) -> ConversationDetail:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/notifications",
        response_model=NotificationPage,
        responses=errors,
        operation_id="preview_list_notifications",
    )
    def notifications(
        cursor: str | None = None, limit: Limit = 20, unread: bool | None = None
    ) -> NotificationPage:
        raise HTTPException(503, "Contract only")

    @app.patch(
        "/api/v1/notifications/{id}/read",
        response_model=Notification,
        responses=errors,
        operation_id="preview_read_notification",
    )
    def read_notification(id: UUID) -> Notification:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/discord/connection",
        response_model=DiscordConnection,
        responses=errors,
        operation_id="preview_get_discord_connection",
    )
    def discord_connection() -> DiscordConnection:
        raise HTTPException(503, "Contract only")
