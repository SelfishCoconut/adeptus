"""Database access for engagements and engagement membership.

Every read path that resolves a single engagement goes through
``get_engagement_for_member`` — never a bare get-by-id — so non-members
receive None (which the service layer turns into 404, per §17.1 isolation).
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.models import User
from app.features.engagements.models import Engagement, EngagementMember


async def create_engagement(
    db: AsyncSession,
    *,
    name: str,
    scope: str,
    client_info: str | None,
    owner_id: UUID,
) -> Engagement:
    """Insert an Engagement row and its owner EngagementMember row in one transaction.

    The caller is responsible for committing the transaction; this function only flushes
    so that the server-generated ``id`` columns are populated before returning.
    """
    engagement = Engagement(name=name, scope=scope, client_info=client_info)
    db.add(engagement)
    await db.flush()  # populate engagement.id

    owner_member = EngagementMember(
        engagement_id=engagement.id,
        user_id=owner_id,
        role="owner",
    )
    db.add(owner_member)
    await db.flush()  # populate owner_member timestamps

    return engagement


async def get_engagement_for_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> Engagement | None:
    """Return the Engagement if ``user_id`` is a member, otherwise None.

    This is the §17.1 isolation chokepoint: every read path for a single
    engagement must go through this function so non-members get None (→ 404),
    not a 403 that would reveal the engagement exists.
    """
    result = await db.execute(
        select(Engagement)
        .join(EngagementMember, EngagementMember.engagement_id == Engagement.id)
        .where(Engagement.id == engagement_id, EngagementMember.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_engagements_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> list[tuple[Engagement, str]]:
    """Return all engagements the user belongs to, paired with their role."""
    result = await db.execute(
        select(Engagement, EngagementMember.role)
        .join(EngagementMember, EngagementMember.engagement_id == Engagement.id)
        .where(EngagementMember.user_id == user_id)
    )
    return [(row[0], row[1]) for row in result.all()]


async def get_members(
    db: AsyncSession,
    engagement_id: UUID,
) -> list[tuple[EngagementMember, str]]:
    """Return all members of the engagement joined with their username."""
    result = await db.execute(
        select(EngagementMember, User.username)
        .join(User, EngagementMember.user_id == User.id)
        .where(EngagementMember.engagement_id == engagement_id)
    )
    return [(row[0], row[1]) for row in result.all()]


async def get_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> EngagementMember | None:
    """Return the EngagementMember row for the given engagement + user, or None."""
    result = await db.execute(
        select(EngagementMember).where(
            EngagementMember.engagement_id == engagement_id,
            EngagementMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def add_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> EngagementMember:
    """Insert a new EngagementMember row with role ``"member"`` and return it."""
    member = EngagementMember(
        engagement_id=engagement_id,
        user_id=user_id,
        role="member",
    )
    db.add(member)
    await db.flush()
    return member


async def remove_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> None:
    """Delete the EngagementMember row for the given engagement + user."""
    await db.execute(
        delete(EngagementMember).where(
            EngagementMember.engagement_id == engagement_id,
            EngagementMember.user_id == user_id,
        )
    )
