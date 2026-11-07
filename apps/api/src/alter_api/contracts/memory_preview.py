"""#22/#25 memory/relationship contract proposal, not a live API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from alter_api.contracts.activity_preview import Limit
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field


class Memory(BaseModel):
    id: UUID
    text: str
    importance: int = Field(ge=1, le=5)
    source_conversation_id: UUID | None
    created_at: datetime
    status: str | None = None


class MemoryPage(BaseModel):
    items: list[Memory]
    next_cursor: str | None


class Relationship(BaseModel):
    other_agent_id: UUID
    other_agent_name: str
    conversation_count: int = Field(ge=0)
    shared_interests: list[str]
    last_met_at: datetime | None
    blocked: bool


class RelationshipPage(BaseModel):
    items: list[Relationship]
    next_cursor: str | None


def register_memory_contract(app: FastAPI, errors: dict[int | str, dict[str, Any]]) -> None:
    @app.get(
        "/api/v1/memories",
        response_model=MemoryPage,
        responses=errors,
        operation_id="preview_list_memories",
    )
    def memories(agent_id: UUID, cursor: str | None = None, limit: Limit = 20) -> MemoryPage:
        raise HTTPException(503, "Contract only")

    @app.delete(
        "/api/v1/memories/{id}",
        status_code=204,
        responses=errors,
        operation_id="preview_delete_memory",
    )
    def delete_memory(id: UUID) -> Response:
        raise HTTPException(503, "Contract only")

    @app.get(
        "/api/v1/relationships",
        response_model=RelationshipPage,
        responses=errors,
        operation_id="preview_list_relationships",
    )
    def relationships(cursor: str | None = None, limit: Limit = 20) -> RelationshipPage:
        raise HTTPException(503, "Contract only")
