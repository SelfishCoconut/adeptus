"""FastAPI routes for the autonomy feature (Slice 18).

Engagement-scoped standing-autonomy grants (membership required — 404 for non-members /
missing engagement, §17.1):
  GET    /api/v1/engagements/{engagement_id}/autonomy-grants
      List the engagement's active grants (the Autonomy panel data source).
  POST   /api/v1/engagements/{engagement_id}/autonomy-grants
      Grant standing autonomy for one reason category (any member, §5.2).
  DELETE /api/v1/engagements/{engagement_id}/autonomy-grants/{grant_id}
      Revoke a grant (any member). Takes effect on the next turn.

Domain exceptions translate via the registered handlers:
  NotFoundError    → 404  (engagement/grant missing OR caller not a member, §17.1)
  ConflictError    → 409  (an active grant already exists for the category)
  BadRequestError  → 400  (non-delegable category — defense-in-depth behind the 422 schema)
  ValidationError  → 422  (request body rejects unclassified_manifest / unknown reason)
"""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.approvals.schemas import ApprovalReason
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.autonomy import service
from app.features.autonomy.schemas import AutonomyGrantCreate, AutonomyGrantRead

router = APIRouter(tags=["autonomy"])


@router.get(
    "/api/v1/engagements/{engagement_id}/autonomy-grants",
    response_model=list[AutonomyGrantRead],
    operation_id="list_autonomy_grants",
)
async def list_autonomy_grants(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sequence[AutonomyGrantRead]:
    """List the engagement's active standing-autonomy grants. Membership required (404)."""
    return await service.list_grants(db, engagement_id=engagement_id, requester=current_user)


@router.post(
    "/api/v1/engagements/{engagement_id}/autonomy-grants",
    response_model=AutonomyGrantRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="grant_autonomy",
)
async def grant_autonomy(
    engagement_id: UUID,
    body: AutonomyGrantCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AutonomyGrantRead:
    """Grant standing autonomy for one reason category (any member, §5.2)."""
    # DelegableReason (wire type) → ApprovalReason (internal); both share the string value.
    return await service.grant(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        reason=ApprovalReason(body.reason.value),
    )


@router.delete(
    "/api/v1/engagements/{engagement_id}/autonomy-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="revoke_autonomy",
)
async def revoke_autonomy(
    engagement_id: UUID,
    grant_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke a standing-autonomy grant (any member). 404 if missing/already revoked."""
    await service.revoke(db, engagement_id=engagement_id, grant_id=grant_id, requester=current_user)
