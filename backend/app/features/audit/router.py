"""FastAPI routes for the audit log: read-only listings (§14).

There is NO write endpoint — the log is append-only and written only internally via
``service.record``. Authorization lives in the service (membership for the engagement
list → 404 for non-members; admin for the global list → 403); domain exceptions
translate to HTTP via the registered handlers (no inline translation here).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.audit import service
from app.features.audit.schemas import AuditAction, AuditPage
from app.features.auth.deps import get_current_user
from app.features.auth.models import User

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=AuditPage, operation_id="list_engagement_audit")
async def list_engagement_audit(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    action: AuditAction | None = None,
    self_approved: bool | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditPage:
    """List an engagement's audit entries, newest-first. Requires membership (404 otherwise)."""
    return await service.list_engagement_audit(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        action=action,
        self_approved=self_approved,
        cursor=cursor,
        limit=limit,
    )


@router.get("/global", response_model=AuditPage, operation_id="list_global_audit")
async def list_global_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    action: AuditAction | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditPage:
    """List instance-global (no-engagement) audit entries, newest-first. Admin only (403)."""
    return await service.list_global_audit(
        db, requester=current_user, action=action, cursor=cursor, limit=limit
    )
