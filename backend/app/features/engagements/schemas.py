"""Pydantic v2 request/response models for the engagements feature."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EngagementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: str = Field(max_length=4096)
    client_info: str | None = Field(default=None, max_length=1024)


class EngagementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: Literal["active", "archived"]
    created_at: datetime
    member_role: Literal["owner", "member"]


class EngagementDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: Literal["active", "archived"]
    scope: str
    client_info: str | None
    created_at: datetime
    updated_at: datetime
    member_role: Literal["owner", "member"]


class MemberEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    role: Literal["owner", "member"]
    joined_at: datetime


class AddMemberRequest(BaseModel):
    username: str
