"""FastAPI routes for the findings feature (Slice 19 task 7). HTTP-level concerns only.

Domain exceptions subclass NotFoundError or ConflictError and are translated to
HTTP codes by the registered core error handlers:

  EngagementNotFound  → NotFoundError  → 404
  FindingNotFound     → NotFoundError  → 404
  LinkedNodeNotFound  → NotFoundError  → 404
  EngagementArchived  → ConflictError  → 409

401 is produced automatically by the get_current_user dependency when the session
cookie is absent or invalid. 422 is produced automatically by Pydantic body
validation. The router does NOT catch or translate any domain exception itself.
"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.findings import service
from app.features.findings.schemas import (
    Finding,
    FindingCreate,
    FindingList,
    FindingUpdate,
    RemediationUpdate,
    VerificationUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["findings"])


# ---------------------------------------------------------------------------
# GET /api/v1/engagements/{engagement_id}/findings — list_findings
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{engagement_id}/findings",
    response_model=FindingList,
    operation_id="list_findings",
)
async def list_findings(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_deleted: Annotated[bool, Query()] = False,
) -> FindingList:
    """List the engagement's findings (newest-first).

    Membership-gated (§17.1 — non-member returns 404). Read-only path: archived
    engagements are accessible. ``include_deleted`` surfaces soft-deleted findings.
    """
    return await service.list_findings(
        db,
        engagement_id=engagement_id,
        user_id=cast(UUID, current_user.id),
        include_deleted=include_deleted,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/engagements/{engagement_id}/findings — create_finding
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{engagement_id}/findings",
    response_model=Finding,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_finding",
)
async def create_finding(
    engagement_id: UUID,
    body: FindingCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Finding:
    """Create a finding with a Simple severity (defaults unverified/open).

    Membership-gated. Returns 409 for archived engagements; 404 when a given
    node_id is not a live node in this engagement; 422 for invalid body.
    """
    return await service.create_finding(
        db,
        engagement_id=engagement_id,
        user_id=cast(UUID, current_user.id),
        payload=body,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/engagements/{engagement_id}/findings/{finding_id} — get_finding
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{engagement_id}/findings/{finding_id}",
    response_model=Finding,
    operation_id="get_finding",
)
async def get_finding(
    engagement_id: UUID,
    finding_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Finding:
    """Get a single finding's detail.

    Membership-gated. Read-only path: archived engagements are accessible.
    Returns 404 when the finding is not found or in another engagement.
    """
    return await service.get_finding(
        db,
        engagement_id=engagement_id,
        finding_id=finding_id,
        user_id=cast(UUID, current_user.id),
    )


# ---------------------------------------------------------------------------
# PATCH /api/v1/engagements/{engagement_id}/findings/{finding_id} — update_finding
# ---------------------------------------------------------------------------


@router.patch(
    "/engagements/{engagement_id}/findings/{finding_id}",
    response_model=Finding,
    operation_id="update_finding",
)
async def update_finding(
    engagement_id: UUID,
    finding_id: UUID,
    body: FindingUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Finding:
    """Update a finding's title, description, severity, and/or node link.

    Membership-gated. Returns 409 for archived engagements; 404 when the finding
    or a given node_id is not found; 422 for an empty/invalid body.
    """
    return await service.update_finding(
        db,
        engagement_id=engagement_id,
        finding_id=finding_id,
        user_id=cast(UUID, current_user.id),
        payload=body,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/engagements/{engagement_id}/findings/{finding_id} — delete_finding
# ---------------------------------------------------------------------------


@router.delete(
    "/engagements/{engagement_id}/findings/{finding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_finding",
)
async def delete_finding(
    engagement_id: UUID,
    finding_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Soft-delete a finding (recoverable via history).

    Returns 204 on success. Membership-gated. Returns 409 for archived
    engagements; 404 when the finding is not found.
    """
    await service.delete_finding(
        db,
        engagement_id=engagement_id,
        finding_id=finding_id,
        user_id=cast(UUID, current_user.id),
    )


# ---------------------------------------------------------------------------
# PATCH .../findings/{finding_id}/verification — set_finding_verification
# ---------------------------------------------------------------------------


@router.patch(
    "/engagements/{engagement_id}/findings/{finding_id}/verification",
    response_model=Finding,
    operation_id="set_finding_verification",
)
async def set_finding_verification(
    engagement_id: UUID,
    finding_id: UUID,
    body: VerificationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Finding:
    """Set verification status (unverified | verified | false_positive).

    Membership-gated. Returns 409 for archived engagements; 404 when not found;
    422 for an invalid status value.
    """
    return await service.set_verification(
        db,
        engagement_id=engagement_id,
        finding_id=finding_id,
        user_id=cast(UUID, current_user.id),
        payload=body,
    )


# ---------------------------------------------------------------------------
# PATCH .../findings/{finding_id}/remediation — set_finding_remediation
# ---------------------------------------------------------------------------


@router.patch(
    "/engagements/{engagement_id}/findings/{finding_id}/remediation",
    response_model=Finding,
    operation_id="set_finding_remediation",
)
async def set_finding_remediation(
    engagement_id: UUID,
    finding_id: UUID,
    body: RemediationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Finding:
    """Set remediation status (open | fixed | risk_accepted).

    Membership-gated. Returns 409 for archived engagements; 404 when not found;
    422 for an invalid status value.
    """
    return await service.set_remediation(
        db,
        engagement_id=engagement_id,
        finding_id=finding_id,
        user_id=cast(UUID, current_user.id),
        payload=body,
    )
