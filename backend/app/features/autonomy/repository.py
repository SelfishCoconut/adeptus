"""Database access for standing-autonomy grants (Slice 18).

Module-level async functions over an ``AsyncSession`` (the caller owns the transaction),
matching the rest of the features. The load-bearing read is :func:`get_active_reasons`,
called once per turn by the approvals service to decide which gated commands auto-approve;
the load-bearing write is :func:`revoke` — a guarded conditional UPDATE so a revoke takes
effect on the very next turn and a double-revoke is a clean no-op.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.autonomy.models import AutonomyGrant


async def create_grant(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    reason: str,
    granted_by_user_id: UUID,
) -> AutonomyGrant:
    """Insert an active grant and return it with server defaults populated.

    The caller (service) pre-checks for an existing active grant to return a clean 409; the
    partial unique index ``(engagement_id, reason) WHERE revoked_at IS NULL`` is the hard
    backstop against a concurrent duplicate. The caller commits.
    """
    grant = AutonomyGrant(
        engagement_id=engagement_id,
        reason=reason,
        granted_by_user_id=granted_by_user_id,
    )
    db.add(grant)
    await db.flush()
    await db.refresh(grant)
    return grant


async def list_active(db: AsyncSession, *, engagement_id: UUID) -> Sequence[AutonomyGrant]:
    """Return the engagement's active (un-revoked) grants, newest-first."""
    result = await db.execute(
        select(AutonomyGrant)
        .where(
            AutonomyGrant.engagement_id == engagement_id,
            AutonomyGrant.revoked_at.is_(None),
        )
        .order_by(desc(AutonomyGrant.created_at), desc(AutonomyGrant.id))
    )
    return list(result.scalars().all())


async def get_active_grant_map(db: AsyncSession, *, engagement_id: UUID) -> dict[str, UUID]:
    """Return ``{reason: grant_id}`` for the engagement's active grants.

    The per-turn lookup the approvals service uses for the auto-approve decision: a gated
    command auto-approves iff *all* its reasons are keys here, and the matching grant ids are
    recorded in the ``approval_auto_granted`` audit payload so an auditor can trace an
    auto-approved action back to the specific grant that authorised it (§14). At most one
    active grant per reason (the partial unique index), so the mapping is unambiguous."""
    result = await db.execute(
        select(AutonomyGrant.reason, AutonomyGrant.id).where(
            AutonomyGrant.engagement_id == engagement_id,
            AutonomyGrant.revoked_at.is_(None),
        )
    )
    return {reason: cast(UUID, grant_id) for reason, grant_id in result.all()}


async def get_active_reasons(db: AsyncSession, *, engagement_id: UUID) -> set[str]:
    """Return the set of reason categories with an active grant for the engagement.

    A thin convenience over :func:`get_active_grant_map` (used widely in tests as the
    canonical "what is active" assertion)."""
    return set(await get_active_grant_map(db, engagement_id=engagement_id))


async def get_active_for_reason(
    db: AsyncSession, *, engagement_id: UUID, reason: str
) -> AutonomyGrant | None:
    """Return the active grant for (engagement, reason), or None — the dup pre-check."""
    result = await db.execute(
        select(AutonomyGrant).where(
            AutonomyGrant.engagement_id == engagement_id,
            AutonomyGrant.reason == reason,
            AutonomyGrant.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    grant_id: UUID,
    revoked_by_user_id: UUID,
) -> AutonomyGrant | None:
    """Revoke an active grant via a guarded UPDATE.

    ``WHERE id=:id AND engagement_id=:eng AND revoked_at IS NULL`` claims the active grant
    atomically: a missing/foreign/already-revoked grant matches 0 rows and returns ``None``
    (the router maps that to 404). The caller commits.
    """
    stmt = (
        update(AutonomyGrant)
        .where(
            AutonomyGrant.id == grant_id,
            AutonomyGrant.engagement_id == engagement_id,
            AutonomyGrant.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_by_user_id=revoked_by_user_id)
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:  # type: ignore[attr-defined]
        return None
    refreshed = await db.execute(select(AutonomyGrant).where(AutonomyGrant.id == grant_id))
    return refreshed.scalar_one_or_none()
