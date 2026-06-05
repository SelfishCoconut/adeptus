"""FastAPI routes for the approvals feature (Slice 16).

Endpoints (all engagement-scoped; membership required — 404 for non-members / missing
engagement, §17.1):
  GET  /api/v1/engagements/{engagement_id}/approvals
      List the engagement's shared approval queue, newest-first, optional status filter.
      The data source for the Approvals tab AND the future Slice-32 notifications bell.
  POST /api/v1/engagements/{engagement_id}/approvals/{request_id}/approve
      Approve a pending dangerous command (any member, incl. the initiator — §5.2). Records
      decider-attributed attribution + self_approved, then runs the command (initiator-
      attributed — Resolved decision 3).
  POST /api/v1/engagements/{engagement_id}/approvals/{request_id}/reject
      Reject a pending command (symmetric). The command is never executed.

Domain exceptions translate via the registered handlers (NotFoundError→404), EXCEPT the two
decision 409s, which the route translates inline to an ``ApprovalConflict`` body so the
single 409 carries a machine-readable reason (same pattern as the chat/mcp 409):
  NotFoundError            → 404  (engagement/request missing OR caller not a member, §17.1)
  AlreadyDecidedError      → 409  reason=already_decided (+ terminal status; Risk 1)
  EngagementArchivedError  → 409  reason=engagement_archived (archived = read-only, §4)
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.approvals import service
from app.features.approvals.schemas import (
    ApprovalConflict,
    ApprovalRequestPage,
    ApprovalRequestRead,
    ApprovalStatus,
)
from app.features.auth.deps import get_current_user
from app.features.auth.models import User

router = APIRouter(tags=["approvals"])

_409_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ApprovalConflict,
        "description": (
            "Either the request is already decided (reason=already_decided, with the "
            "terminal status), OR the engagement is archived (reason=engagement_archived, "
            "no new runs §4)."
        ),
    },
}


@router.get(
    "/api/v1/engagements/{engagement_id}/approvals",
    response_model=ApprovalRequestPage,
    operation_id="list_approval_requests",
)
async def list_approval_requests(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApprovalRequestPage:
    """List the engagement's shared approval requests (§5.2). Membership required (404)."""
    return await service.list_requests(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/api/v1/engagements/{engagement_id}/approvals/{request_id}/approve",
    response_model=ApprovalRequestRead,
    operation_id="approve_request",
    responses=_409_RESPONSE,
)
async def approve_request(
    engagement_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApprovalRequestRead | JSONResponse:
    """Approve a pending dangerous command and hand it to the tool-run pipeline (§5.2)."""
    return await _decide(db, engagement_id, request_id, current_user, "approve")


@router.post(
    "/api/v1/engagements/{engagement_id}/approvals/{request_id}/reject",
    response_model=ApprovalRequestRead,
    operation_id="reject_request",
    responses=_409_RESPONSE,
)
async def reject_request(
    engagement_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApprovalRequestRead | JSONResponse:
    """Reject a pending dangerous command; it is never executed (§5.2 symmetric)."""
    return await _decide(db, engagement_id, request_id, current_user, "reject")


async def _decide(
    db: AsyncSession,
    engagement_id: UUID,
    request_id: UUID,
    requester: User,
    decision: Literal["approve", "reject"],
) -> ApprovalRequestRead | JSONResponse:
    try:
        result = await service.decide(
            db,
            engagement_id=engagement_id,
            request_id=request_id,
            requester=requester,
            decision=decision,
        )
    except service.AlreadyDecidedError as exc:
        return _conflict("already_decided", status_value=ApprovalStatus(exc.status))
    except service.EngagementArchivedError:
        return _conflict("engagement_archived")
    # service.decide owns its commit (explicit ordering — Risk 4); no second commit here.
    return result


def _conflict(
    reason: Literal["already_decided", "engagement_archived"],
    *,
    status_value: ApprovalStatus | None = None,
) -> JSONResponse:
    body = ApprovalConflict(reason=reason, status=status_value)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body.model_dump(mode="json"))
