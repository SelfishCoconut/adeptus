"""Service layer for standing-autonomy grants (Slice 18).

Three entry points — :func:`grant`, :func:`list_grants`, :func:`revoke` — each guarded by
engagement membership (§17.1 chokepoint: non-members get 404, not 403). Grant and revoke
emit their audit action (``autonomy_granted`` / ``autonomy_revoked``) atomically with the
write. ``unclassified_manifest`` is never delegable (defense-in-depth beyond the schema
validator). The per-turn auto-approve decision itself lives in the approvals service
(it reads :func:`repository.get_active_reasons`); this module owns the grant lifecycle.
"""

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.features.approvals.schemas import ApprovalReason
from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.autonomy import repository as repo
from app.features.autonomy.models import DELEGABLE_REASONS, AutonomyGrant
from app.features.autonomy.schemas import AutonomyGrantRead
from app.features.engagements import repository as eng_repo


def _user_id(user: User) -> UUID:
    return cast(UUID, user.id)


async def _require_member(db: AsyncSession, engagement_id: UUID, requester: User) -> None:
    """Raise NotFoundError unless ``requester`` is a member of the engagement (§17.1)."""
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")


async def _to_read(
    db: AsyncSession,
    grant: AutonomyGrant,
    *,
    username_cache: dict[UUID, str | None] | None = None,
) -> AutonomyGrantRead:
    """Build the read schema, resolving ``granted_by_username`` (read-time convenience)."""
    read = AutonomyGrantRead.model_validate(grant)
    granted_by = grant.granted_by_user_id
    if granted_by is not None:
        cache = username_cache if username_cache is not None else {}
        key = cast(UUID, granted_by)
        if key not in cache:
            user = await auth_repo.get_user_by_id(db, key)
            cache[key] = user.username if user is not None else None
        read = read.model_copy(update={"granted_by_username": cache[key]})
    return read


async def grant(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    reason: ApprovalReason,
) -> AutonomyGrantRead:
    """Grant standing autonomy for one reason category (any engagement member, §5.2).

    Guards: membership → delegable-category → no existing active grant for the category.
    Emits ``autonomy_granted`` atomically with the insert.
    """
    await _require_member(db, engagement_id, requester)

    # Defense-in-depth: the schema validator already rejects unclassified_manifest, but the
    # service must never persist a non-delegable grant even if called directly.
    if reason.value not in DELEGABLE_REASONS:
        raise BadRequestError(f"{reason.value} is not a delegable category")

    existing = await repo.get_active_for_reason(
        db, engagement_id=engagement_id, reason=reason.value
    )
    if existing is not None:
        raise ConflictError(f"Standing autonomy is already active for {reason.value}")

    grant_row = await repo.create_grant(
        db,
        engagement_id=engagement_id,
        reason=reason.value,
        granted_by_user_id=_user_id(requester),
    )
    await audit_service.record(
        db,
        action=AuditAction.AUTONOMY_GRANTED,
        actor_user_id=_user_id(requester),
        engagement_id=engagement_id,
        target_type="autonomy_grant",
        target_id=str(grant_row.id),
        payload={"reason": reason.value},
    )
    await db.commit()
    return await _to_read(db, grant_row)


async def list_grants(
    db: AsyncSession, *, engagement_id: UUID, requester: User
) -> Sequence[AutonomyGrantRead]:
    """Return the engagement's active grants (members only), newest-first."""
    await _require_member(db, engagement_id, requester)
    grants = await repo.list_active(db, engagement_id=engagement_id)
    cache: dict[UUID, str | None] = {}
    return [await _to_read(db, g, username_cache=cache) for g in grants]


async def revoke(db: AsyncSession, *, engagement_id: UUID, grant_id: UUID, requester: User) -> None:
    """Revoke an active grant (any engagement member). 404 if missing/already-revoked.

    Emits ``autonomy_revoked`` atomically with the guarded UPDATE; the revoke takes effect
    on the very next turn (the approvals service re-reads active grants each turn).
    """
    await _require_member(db, engagement_id, requester)
    revoked = await repo.revoke(
        db,
        engagement_id=engagement_id,
        grant_id=grant_id,
        revoked_by_user_id=_user_id(requester),
    )
    if revoked is None:
        raise NotFoundError("Autonomy grant not found")
    await audit_service.record(
        db,
        action=AuditAction.AUTONOMY_REVOKED,
        actor_user_id=_user_id(requester),
        engagement_id=engagement_id,
        target_type="autonomy_grant",
        target_id=str(grant_id),
        payload={"reason": revoked.reason},
    )
    await db.commit()
