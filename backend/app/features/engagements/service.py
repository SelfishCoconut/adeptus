"""Business logic for engagement CRUD and membership; raises domain exceptions.

All functions receive the authenticated caller as a User object.  Domain rules:

- Any authenticated user may create an engagement (§3 restricts admin-only to
  user management, not engagement creation).
- Membership read/write paths go through get_engagement_for_member so that
  non-members receive NotFoundError (404) and cannot infer whether an
  engagement exists (§17.1 isolation posture).
- Only the engagement owner may add or remove members.
- The owner cannot remove themselves (would leave the engagement ownerless).

Callers (routers) are responsible for committing the session after each
mutating call; this service layer only flushes via the repository.
"""

from typing import Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.engagements import events
from app.features.engagements import repository as repo
from app.features.engagements.schemas import (
    AddMemberRequest,
    EngagementCreate,
    EngagementDetail,
    EngagementPauseState,
    EngagementSummary,
    EngagementUpdate,
    MemberEntry,
)


async def create_engagement(
    db: AsyncSession,
    caller: User,
    data: EngagementCreate,
) -> EngagementDetail:
    """Create a new engagement; the caller is automatically set as owner.

    No role restriction — any authenticated user may create an engagement.
    Returns EngagementDetail with member_role="owner".
    """
    engagement = await repo.create_engagement(
        db,
        name=data.name,
        scope=data.scope,
        client_info=data.client_info,
        owner_id=cast(UUID, caller.id),
        privacy_mode=data.privacy_mode,
    )
    return EngagementDetail(
        id=cast(UUID, engagement.id),
        name=engagement.name,
        status=cast(Literal["active", "archived"], engagement.status),
        scope=engagement.scope,
        client_info=engagement.client_info,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        member_role="owner",
        privacy_mode=engagement.privacy_mode,
        concurrency_slot_limit=engagement.concurrency_slot_limit,
        paused=engagement.paused,
    )


async def get_engagement(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
) -> EngagementDetail:
    """Return EngagementDetail for the caller.

    Raises NotFoundError if the engagement does not exist or the caller is not
    a member — membership is never revealed to non-members (§17.1).

    Uses a single JOIN query to fetch both the Engagement and the caller's
    EngagementMember row — no second round-trip is needed for the role.
    """
    row = await repo.get_engagement_for_member(db, engagement_id, cast(UUID, caller.id))
    if row is None:
        raise NotFoundError("Engagement not found")

    engagement, caller_member = row
    member_role = cast(Literal["owner", "member"], caller_member.role)

    return EngagementDetail(
        id=cast(UUID, engagement.id),
        name=engagement.name,
        status=cast(Literal["active", "archived"], engagement.status),
        scope=engagement.scope,
        client_info=engagement.client_info,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        member_role=member_role,
        privacy_mode=engagement.privacy_mode,
        concurrency_slot_limit=engagement.concurrency_slot_limit,
        paused=engagement.paused,
    )


async def update_engagement(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
    data: EngagementUpdate,
) -> EngagementDetail:
    """Update engagement settings (owner only).

    Access rules follow §17.1 isolation posture:
    - Non-member → NotFoundError (do not reveal that the engagement exists).
    - Member but not owner → ForbiddenError.

    If ``data.privacy_mode`` is None the engagement is returned unchanged.
    """
    row = await repo.get_engagement_for_member(db, engagement_id, cast(UUID, caller.id))
    if row is None:
        raise NotFoundError("Engagement not found")

    engagement, caller_member = row
    member_role = cast(Literal["owner", "member"], caller_member.role)

    if member_role != "owner":
        raise ForbiddenError("Only the engagement owner may update engagement settings")

    if data.privacy_mode is not None or data.concurrency_slot_limit is not None:
        updated = await repo.update_engagement(
            db,
            engagement_id,
            privacy_mode=data.privacy_mode,
            concurrency_slot_limit=data.concurrency_slot_limit,
        )
        if updated is None:  # extremely unlikely race; handle defensively
            raise NotFoundError("Engagement not found")
        engagement = updated
        # If the slot limit changed, emit an event so the in-process admission
        # manager (registered as a listener at app startup) re-scans and promptly
        # admits eligible waiters.  The engagements feature stays ignorant of mcp;
        # the dependency flows mcp → engagements via this seam (Finding W1).
        if data.concurrency_slot_limit is not None:
            events.emit_slot_limit_changed(
                engagement_id, cast(int, engagement.concurrency_slot_limit)
            )

    return EngagementDetail(
        id=cast(UUID, engagement.id),
        name=engagement.name,
        status=cast(Literal["active", "archived"], engagement.status),
        scope=engagement.scope,
        client_info=engagement.client_info,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        member_role=member_role,
        privacy_mode=engagement.privacy_mode,
        concurrency_slot_limit=engagement.concurrency_slot_limit,
        paused=engagement.paused,
    )


async def list_engagements(
    db: AsyncSession,
    caller: User,
) -> list[EngagementSummary]:
    """Return all engagements the caller belongs to, with their role in each."""
    rows = await repo.list_engagements_for_user(db, cast(UUID, caller.id))
    return [
        EngagementSummary(
            id=cast(UUID, eng.id),
            name=eng.name,
            status=cast(Literal["active", "archived"], eng.status),
            created_at=eng.created_at,
            member_role=cast(Literal["owner", "member"], role),
            privacy_mode=eng.privacy_mode,
            paused=eng.paused,
        )
        for eng, role in rows
    ]


async def list_members(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
) -> list[MemberEntry]:
    """Return all members of the engagement.

    Raises NotFoundError if the engagement does not exist or the caller is not
    a member — membership is never revealed to non-members (§17.1).
    """
    membership = await repo.get_member(db, engagement_id, cast(UUID, caller.id))
    if membership is None:
        raise NotFoundError("Engagement not found")

    rows = await repo.get_members(db, engagement_id)
    return [
        MemberEntry(
            user_id=cast(UUID, member.user_id),
            username=username,
            role=cast(Literal["owner", "member"], member.role),
            joined_at=member.joined_at,
        )
        for member, username in rows
    ]


async def add_member(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
    request: AddMemberRequest,
) -> MemberEntry:
    """Add a user (by username) to the engagement.

    Checks (ordered to preserve §17.1 isolation):
    1. Caller must be a member; else NotFoundError (do not reveal existence).
    2. Caller must be the owner; else ForbiddenError.
    3. Target username must exist; else NotFoundError.
    4. Target must not already be a member; else ConflictError.
    """
    caller_member = await repo.get_member(db, engagement_id, cast(UUID, caller.id))
    if caller_member is None:
        raise NotFoundError("Engagement not found")
    if caller_member.role != "owner":
        raise ForbiddenError("Only the engagement owner may add members")

    target_user = await auth_repo.get_user_by_username(db, request.username)
    if target_user is None:
        raise NotFoundError("User not found")

    existing = await repo.get_member(db, engagement_id, cast(UUID, target_user.id))
    if existing is not None:
        raise ConflictError("User is already a member of this engagement")

    new_member = await repo.add_member(db, engagement_id, cast(UUID, target_user.id))
    return MemberEntry(
        user_id=cast(UUID, new_member.user_id),
        username=target_user.username,
        role=cast(Literal["owner", "member"], new_member.role),
        joined_at=new_member.joined_at,
    )


async def set_engagement_paused(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
    paused: bool,
) -> EngagementPauseState:
    """Set or clear the engagement-wide tool pause.

    Dependency direction (Slice 06 cite): the engagements service emits
    ``engagement_paused_changed``; the mcp listener (registered at the
    composition root in ``app/main.py``) performs the in-process kills and
    returns ``(killed_running, dequeued)`` counts via the event-dispatch return
    value.  The engagements feature does NOT import the mcp feature — the
    dependency flows mcp → engagements.

    Membership gate: non-members receive NotFoundError (404) and cannot infer
    whether the engagement exists (§17.1 isolation posture).  Any member (not
    owner-only) may pause/resume — Decision 7.

    Idempotent: setting the same pause state twice is a no-op success.  On
    resume (``paused=False``) counts are ``(0, 0)`` because ``set_paused`` on
    the concurrency module only clears the flag when resuming.
    """
    row = await repo.get_engagement_for_member(db, engagement_id, cast(UUID, caller.id))
    if row is None:
        raise NotFoundError("Engagement not found")

    engagement, _ = row

    # Persist the paused flag.  Even if the value is unchanged (idempotent)
    # we still run the DB update so the response reflects the current DB state.
    updated = await repo.update_paused(db, engagement_id, paused)
    if updated is None:  # extremely unlikely race
        raise NotFoundError("Engagement not found")

    # Emit the event; collect (killed_running, dequeued) from listeners.
    # The recommended approach (Slice 06 task 7): the mcp listener calls
    # concurrency.set_paused and returns its (killed_running, dequeued) tuple.
    # We aggregate all listener results; in production there is exactly one
    # listener (mcp), so we sum across all of them for robustness.
    results = events.emit_engagement_paused_changed(engagement_id, paused)
    killed_running = sum(r[0] for r in results)
    dequeued = sum(r[1] for r in results)

    return EngagementPauseState(
        engagement_id=engagement_id,
        paused=paused,
        killed_running=killed_running,
        dequeued=dequeued,
    )


async def remove_member(
    db: AsyncSession,
    caller: User,
    engagement_id: UUID,
    user_id: UUID,
) -> None:
    """Remove a user from the engagement.

    Checks (ordered to preserve §17.1 isolation):
    1. Caller must be a member; else NotFoundError (do not reveal existence).
    2. Caller must be the owner; else ForbiddenError.
    3. Caller cannot remove themselves (owner self-removal); else BadRequestError.
    4. Target member must exist; else NotFoundError.
    """
    caller_member = await repo.get_member(db, engagement_id, cast(UUID, caller.id))
    if caller_member is None:
        raise NotFoundError("Engagement not found")
    if caller_member.role != "owner":
        raise ForbiddenError("Only the engagement owner may remove members")

    if user_id == cast(UUID, caller.id):
        raise BadRequestError("The engagement owner cannot remove themselves")

    target_member = await repo.get_member(db, engagement_id, user_id)
    if target_member is None:
        raise NotFoundError("Member not found")

    await repo.remove_member(db, engagement_id, user_id)
