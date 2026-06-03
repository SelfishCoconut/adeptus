"""FastAPI routes for engagement CRUD and membership management.

All endpoints require an authenticated user via get_current_user.
Domain exceptions raised by the service layer are translated to HTTP responses
by the registered error handlers in app.core.errors.handlers:

    NotFoundError     → 404
    ForbiddenError    → 403
    ConflictError     → 409
    BadRequestError   → 400
    AuthenticationError → 401  (raised by get_current_user when no valid session)

This router does NOT call app.include_router itself — that is task 6.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.engagements import service
from app.features.engagements.schemas import (
    AddMemberRequest,
    EngagementCreate,
    EngagementDetail,
    EngagementPauseRequest,
    EngagementPauseState,
    EngagementSummary,
    EngagementUpdate,
    MemberEntry,
)

router = APIRouter(prefix="/api/v1/engagements", tags=["engagements"])


@router.get("", response_model=list[EngagementSummary], operation_id="list_engagements")
async def list_engagements(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[EngagementSummary]:
    """List engagements the caller is a member of."""
    return await service.list_engagements(db, current_user)


@router.post(
    "",
    response_model=EngagementDetail,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_engagement",
)
async def create_engagement(
    body: EngagementCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EngagementDetail:
    """Create a new engagement; caller becomes owner."""
    detail = await service.create_engagement(db, current_user, body)
    await db.commit()
    return detail


@router.patch(
    "/{engagement_id}",
    response_model=EngagementDetail,
    operation_id="update_engagement",
)
async def update_engagement(
    engagement_id: UUID,
    body: EngagementUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EngagementDetail:
    """Update engagement settings (owner only)."""
    detail = await service.update_engagement(db, current_user, engagement_id, body)
    await db.commit()
    return detail


@router.get("/{engagement_id}", response_model=EngagementDetail, operation_id="get_engagement")
async def get_engagement(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EngagementDetail:
    """Get a single engagement (caller must be a member)."""
    return await service.get_engagement(db, current_user, engagement_id)


@router.get(
    "/{engagement_id}/members",
    response_model=list[MemberEntry],
    operation_id="list_members",
)
async def list_members(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[MemberEntry]:
    """List members of an engagement (caller must be a member)."""
    return await service.list_members(db, current_user, engagement_id)


@router.post(
    "/{engagement_id}/members",
    response_model=MemberEntry,
    status_code=status.HTTP_201_CREATED,
    operation_id="add_member",
)
async def add_member(
    engagement_id: UUID,
    body: AddMemberRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemberEntry:
    """Invite a user to the engagement (owner only)."""
    entry = await service.add_member(db, current_user, engagement_id, body)
    await db.commit()
    return entry


@router.delete(
    "/{engagement_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_member",
)
async def remove_member(
    engagement_id: UUID,
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Remove a member from the engagement (owner only)."""
    await service.remove_member(db, current_user, engagement_id, user_id)
    await db.commit()


@router.post(
    "/{engagement_id}/pause",
    response_model=EngagementPauseState,
    operation_id="set_engagement_paused",
)
async def set_engagement_paused(
    engagement_id: UUID,
    body: EngagementPauseRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EngagementPauseState:
    """Set or clear the engagement-wide tool pause.

    When ``paused=true``, every in-flight tool run for the engagement is killed,
    every queued run is de-queued, and all subsequent POST /tool-runs are
    rejected 409 until resumed.  When ``paused=false``, new runs are allowed
    again (already-killed runs are NOT resumed).  Membership-gated (§17.1).
    Idempotent: setting the same state twice is a no-op success.
    """
    result = await service.set_engagement_paused(db, current_user, engagement_id, body.paused)
    await db.commit()
    return result
