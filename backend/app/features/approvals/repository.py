"""Database access for approval requests (Slice 16).

Module-level async functions over an ``AsyncSession``, matching the rest of the
features (flush/refresh for server defaults, ``select()`` + ``execute()`` for reads,
keyset pagination via ``(created_at DESC, id DESC)``). The caller owns the transaction.

The load-bearing function is :func:`decide_request` — a **guarded** conditional UPDATE
(``WHERE id=:id AND status='pending'``) that atomically claims a pending request for the
winning decider; a double-/concurrent-decision finds 0 rows and returns ``None`` so the
dangerous command can never run twice (Risk 1).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.approvals.models import ApprovalRequest


async def create_request(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    chat_message_id: UUID,
    initiator_user_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    reasons: Sequence[str],
    preset_name: str | None = None,
    rationale: str | None = None,
) -> ApprovalRequest:
    """Insert a ``pending`` approval request and return it with server defaults populated.

    ``reasons`` is stored verbatim (the ``ApprovalReason`` *values*); ``args`` is stored
    verbatim with no redaction (§5.5). The caller is responsible for committing.
    """
    request = ApprovalRequest(
        engagement_id=engagement_id,
        chat_message_id=chat_message_id,
        initiator_user_id=initiator_user_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        reasons=list(reasons),
        preset_name=preset_name,
        rationale=rationale,
    )
    db.add(request)
    await db.flush()
    await db.refresh(request)
    return request


async def get_request_for_engagement(
    db: AsyncSession, *, engagement_id: UUID, request_id: UUID
) -> ApprovalRequest | None:
    """Return the request iff it exists AND belongs to the engagement (§17.1)."""
    result = await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.id == request_id,
            ApprovalRequest.engagement_id == engagement_id,
        )
    )
    return result.scalar_one_or_none()


async def list_for_engagement(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    status: str | None = None,
    cursor: tuple[datetime, UUID] | None = None,
    limit: int = 50,
) -> tuple[list[ApprovalRequest], tuple[datetime, UUID] | None]:
    """Return an engagement's requests newest-first, optionally status-filtered.

    Ordering is ``(created_at DESC, id DESC)`` — id is a deterministic tiebreak. Fetches
    ``limit + 1`` rows to detect a next page; the ``or_``/``and_`` keyset form is
    Postgres- and SQLite-compatible. ``next_cursor`` is the ``(created_at, id)`` of the
    last returned row, or ``None`` when the page is the last.
    """
    stmt = (
        select(ApprovalRequest)
        .where(ApprovalRequest.engagement_id == engagement_id)
        .order_by(desc(ApprovalRequest.created_at), desc(ApprovalRequest.id))
        .limit(limit + 1)
    )
    if status is not None:
        stmt = stmt.where(ApprovalRequest.status == status)
    if cursor is not None:
        c_created, c_id = cursor
        stmt = stmt.where(
            or_(
                ApprovalRequest.created_at < c_created,
                and_(ApprovalRequest.created_at == c_created, ApprovalRequest.id < c_id),
            )
        )

    rows = list((await db.execute(stmt)).scalars().all())
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor: tuple[datetime, UUID] | None = (last.created_at, last.id)  # type: ignore[assignment]
    else:
        next_cursor = None
    return rows, next_cursor


async def list_for_chat_message(db: AsyncSession, *, message_id: UUID) -> list[ApprovalRequest]:
    """Return all approval requests created by one assistant turn (oldest-first).

    Drives the inline card re-render on history reload (joins on ``chat_message_id``).
    """
    result = await db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.chat_message_id == message_id)
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
    )
    return list(result.scalars().all())


async def decide_request(
    db: AsyncSession,
    *,
    request_id: UUID,
    status: str,
    acted_by_user_id: UUID,
    self_approved: bool,
    tool_run_id: UUID | None = None,
) -> ApprovalRequest | None:
    """Atomically transition a PENDING request to a terminal status, or return ``None``.

    The ``WHERE id=:id AND status='pending'`` predicate makes this the single point that
    claims a request: exactly one caller's UPDATE affects a row; any later/concurrent
    decision affects 0 rows and gets ``None`` (the double-decision guard, Risk 1). The
    caller emits the audit entry in the SAME transaction and only the winner creates the
    run. ``decided_at`` is stamped here.
    """
    result = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == request_id, ApprovalRequest.status == "pending")
        .values(
            status=status,
            acted_by_user_id=acted_by_user_id,
            self_approved=self_approved,
            tool_run_id=tool_run_id,
            decided_at=datetime.now(tz=UTC),
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        return None
    fresh = await db.execute(select(ApprovalRequest).where(ApprovalRequest.id == request_id))
    return fresh.scalar_one()
