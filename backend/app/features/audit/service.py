"""Audit recording chokepoint + read services.

``record()`` is the ONLY way to write an audit entry. Callers in other features
invoke it after their own state change, within the SAME transaction (Decision 1,
atomic): the audit row commits or rolls back with the originating action, so there
are no silent gaps and no orphaned entries (§14 "records *every* ...").

Live callers wired by this slice (Slice 10):
  * auth.router — ``login`` / ``logout`` / ``login_failed``.
  * mcp.service.execute_tool_run — ``tool_run`` (both paths) + ``tool_run_completed``
    (sync path; async/background completion is a documented follow-up).
  * graph.service — ``graph_node_*`` / ``graph_edge_*`` at the ``_push_undo`` chokepoint
    (every ordinary node/edge write) AND at ``pop_undo_stack`` for undo-applied inverses.
    This wires the Slice 09 audit seams (its ``push_undo_entry`` / ``pop_undo_stack``
    chokepoints): an undo-applied write bypasses the public mutators (it goes straight
    through the single writer), so it is recorded once at ``pop_undo_stack`` and is NOT
    double-counted by the ``_push_undo`` emission.

Reserved seams — defined in ``AuditAction``, accepted by ``record()``, with NO caller in
this slice (downstream slices add the caller; this module imports neither feature):
  * Slice 16 (approval flow) → ``record(action=AuditAction.APPROVAL_GRANTED |
    APPROVAL_REJECTED, self_approved=<initiator == approver>, ...)`` (§5.2).
  * Slice 11+ (AI integration) → ``record(action=AuditAction.AI_CALL, ...)``.
"""

import base64
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.features.audit import repository
from app.features.audit.models import AuditEntry
from app.features.audit.schemas import AuditAction, AuditEntryRead, AuditPage
from app.features.auth.models import User
from app.features.engagements import repository as eng_repo


async def record(
    db: AsyncSession,
    *,
    action: AuditAction | str,
    actor_user_id: UUID | None = None,
    engagement_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    self_approved: bool | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEntry:
    """Append one audit entry and return it (flushed, NOT committed — the caller
    commits it atomically with the originating action).

    ``action`` is validated against ``AuditAction`` and normalized to its plain string
    value, so the stored + hashed action is always canonical and an unknown action
    fails loudly here rather than silently writing garbage into the chain.
    """
    action_value = AuditAction(action).value
    return await repository.append_entry(
        db,
        action=action_value,
        actor_user_id=actor_user_id,
        engagement_id=engagement_id,
        target_type=target_type,
        target_id=target_id,
        self_approved=self_approved,
        payload=payload,
    )


async def list_engagement_audit(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    action: AuditAction | None = None,
    self_approved: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> AuditPage:
    """List an engagement's audit entries, newest-first. Membership chokepoint:
    non-members (and missing engagements) get NotFoundError → 404 (§17.1)."""
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")

    rows, next_seq = await repository.list_for_engagement(
        db,
        engagement_id=engagement_id,
        action=action.value if action else None,
        self_approved=self_approved,
        cursor_seq=_decode_cursor(cursor),
        limit=limit,
    )
    return _to_page(rows, next_seq)


async def list_global_audit(
    db: AsyncSession,
    *,
    requester: User,
    action: AuditAction | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> AuditPage:
    """List instance-global (no-engagement) audit entries, newest-first. Admin only —
    non-admins get ForbiddenError → 403 (§14 is an admin/forensic surface)."""
    if requester.role != "admin":
        raise ForbiddenError("Admin privileges required")

    rows, next_seq = await repository.list_global(
        db,
        action=action.value if action else None,
        cursor_seq=_decode_cursor(cursor),
        limit=limit,
    )
    return _to_page(rows, next_seq)


def _user_id(user: User) -> UUID:
    # User.id is a Mapped[UUID]; cast (not type:ignore) to satisfy both mypy configs —
    # see the project's "mypy two configs diverge" convention.
    return cast(UUID, user.id)


def _to_page(rows: list[AuditEntry], next_seq: int | None) -> AuditPage:
    return AuditPage(
        items=[AuditEntryRead.model_validate(r) for r in rows],
        next_cursor=_encode_cursor(next_seq) if next_seq is not None else None,
    )


def _encode_cursor(seq: int) -> str:
    """Encode a seq position as an opaque base64url cursor."""
    return base64.urlsafe_b64encode(str(seq).encode()).decode()


def _decode_cursor(cursor: str | None) -> int | None:
    """Decode an opaque cursor back to a seq position; None passes through.

    A malformed cursor is a client error → BadRequestError (400)."""
    if cursor is None:
        return None
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, TypeError) as exc:
        raise BadRequestError("Malformed cursor") from exc
