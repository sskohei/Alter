"""Explicit public projection: private information is never part of ProfileRevision."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(max_length=2000)]
Item = Annotated[str, Field(min_length=1, max_length=100)]
Items = Annotated[list[Item], Field(max_length=20)]


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personality: Text
    values_text: Text
    interests: Items
    speaking_style: Text
    interaction_policy: Text
    shareable_info: Text
    restrictions: Text
    hobbies: Items = Field(default_factory=list)
    strengths: Items = Field(default_factory=list)
    weaknesses: Items = Field(default_factory=list)


class AgentDraftInput(ProfileInput):
    name: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    private_info: Text = Field(default="", repr=False)
    # Fail closed until the Storage metadata/ownership adapter exists.
    icon_path: None = None


class RevisionInput(AgentDraftInput):
    expected_version: int = Field(ge=1)


class ProfileRevision(ProfileInput):
    version: int
    approved_at: datetime | None


class Agent(BaseModel):
    id: UUID
    name: str
    status: Literal["draft", "active", "paused", "deleted"]
    approved_profile_version: int | None
    created_at: datetime
    icon_url: None = None


class AgentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    icon_path: None = None


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class PrivateInfo(BaseModel):
    private_info: str = Field(repr=False)
