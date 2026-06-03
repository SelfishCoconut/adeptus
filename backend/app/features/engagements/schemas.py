"""Pydantic v2 request/response models for the engagements feature."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PrivacyMode = Literal["local_only", "cloud_enabled"]


class EngagementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: str = Field(max_length=4096)
    client_info: str | None = Field(default=None, max_length=1024)
    privacy_mode: PrivacyMode = "local_only"


class EngagementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: Literal["active", "archived"]
    created_at: datetime
    member_role: Literal["owner", "member"]
    privacy_mode: PrivacyMode
    paused: bool = False


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
    privacy_mode: PrivacyMode
    concurrency_slot_limit: int
    paused: bool = False


class EngagementUpdate(BaseModel):
    privacy_mode: PrivacyMode | None = None
    concurrency_slot_limit: int | None = Field(default=None, ge=1, le=16)


class MemberEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    role: Literal["owner", "member"]
    joined_at: datetime


class AddMemberRequest(BaseModel):
    username: str


# ---------------------------------------------------------------------------
# Engagement pause schemas (Slice 06)
# ---------------------------------------------------------------------------


class EngagementPauseRequest(BaseModel):
    """Request body for POST /api/v1/engagements/{id}/pause."""

    paused: bool


class EngagementPauseState(BaseModel):
    """Response body for POST /api/v1/engagements/{id}/pause.

    ``killed_running`` — number of in-flight runs (including runs awaiting a
    timeout decision) that were killed by this pause action; 0 when resuming or
    when the engagement was already paused.
    ``dequeued`` — number of queued runs removed by this pause action.
    """

    engagement_id: UUID
    paused: bool
    killed_running: int
    dequeued: int
