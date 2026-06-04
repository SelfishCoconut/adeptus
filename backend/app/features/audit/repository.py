"""Append-only, hash-chained writes + read queries for the audit log.

There is intentionally **no** update or delete function — the log is append-only
(§14). The sole mutation is ``append_entry``, which serializes appends under the
``audit_chain_head`` row lock (``SELECT ... FOR UPDATE``) inside the caller's
transaction, so ``seq`` and ``prev_hash`` are assigned under a strict total order and
the chain cannot fork under concurrency (Risk 1). ``seq`` and ``entry_hash`` are also
UNIQUE, so any residual fork hard-fails at the DB.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.audit.hashing import GENESIS_HASH, AuditContent, compute_entry_hash
from app.features.audit.models import AuditChainHead, AuditEntry


async def get_chain_head(db: AsyncSession) -> AuditChainHead | None:
    """Return the singleton chain-head row (authoritative last_seq + head_hash), or None.

    Read-only — used by the verifier to detect tail truncation (the head still points
    at a seq/hash that no longer exists in audit_entries)."""
    return (
        await db.execute(select(AuditChainHead).where(AuditChainHead.id == 1))
    ).scalar_one_or_none()


async def append_entry(
    db: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None = None,
    engagement_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    self_approved: bool | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEntry:
    """Append one entry to the chain and return it (flushed, NOT committed).

    The caller commits in the same transaction as the originating action (Decision 1,
    atomic). ``created_at`` is generated here in Python (microsecond precision) and
    inserted explicitly so the value that was hashed is exactly the value stored — the
    server-side ``now()`` default is never used for an actual append (it would not be
    knowable at hash-compute time). The ``FOR UPDATE`` lock is held until the caller's
    commit, which is what serializes concurrent appenders.

    The genesis head row is seeded by the migration in production; we self-seed it here
    if absent so a caller's first append never fails on a fresh/test DB. The singleton
    ``CHECK(id = 1)`` + PK keep this idempotent.
    """
    head = (
        await db.execute(select(AuditChainHead).where(AuditChainHead.id == 1).with_for_update())
    ).scalar_one_or_none()
    if head is None:
        head = AuditChainHead(id=1, last_seq=0, head_hash=GENESIS_HASH)
        db.add(head)
        await db.flush()

    seq = head.last_seq + 1
    created_at = datetime.now(UTC)
    content = AuditContent(
        seq=seq,
        created_at=created_at,
        action=action,
        actor_user_id=actor_user_id,
        engagement_id=engagement_id,
        target_type=target_type,
        target_id=target_id,
        self_approved=self_approved,
        payload=payload or {},
    )
    entry_hash = compute_entry_hash(head.head_hash, content)

    entry = AuditEntry(
        seq=seq,
        action=action,
        actor_user_id=actor_user_id,
        engagement_id=engagement_id,
        target_type=target_type,
        target_id=target_id,
        self_approved=self_approved,
        payload=payload or {},
        prev_hash=head.head_hash,
        entry_hash=entry_hash,
        created_at=created_at,
    )
    db.add(entry)
    head.last_seq = seq
    head.head_hash = entry_hash
    await db.flush()
    return entry


async def list_for_engagement(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    action: str | None = None,
    self_approved: bool | None = None,
    cursor_seq: int | None = None,
    limit: int = 50,
) -> tuple[list[AuditEntry], int | None]:
    """Return (entries, next_cursor_seq) for an engagement, newest-first (seq DESC)."""
    stmt = select(AuditEntry).where(AuditEntry.engagement_id == engagement_id)
    if action is not None:
        stmt = stmt.where(AuditEntry.action == action)
    if self_approved is not None:
        stmt = stmt.where(AuditEntry.self_approved.is_(self_approved))
    if cursor_seq is not None:
        stmt = stmt.where(AuditEntry.seq < cursor_seq)
    stmt = stmt.order_by(AuditEntry.seq.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    return _split_page(rows, limit)


async def list_global(
    db: AsyncSession,
    *,
    action: str | None = None,
    cursor_seq: int | None = None,
    limit: int = 50,
) -> tuple[list[AuditEntry], int | None]:
    """Return (entries, next_cursor_seq) for instance-global (no-engagement) events."""
    stmt = select(AuditEntry).where(AuditEntry.engagement_id.is_(None))
    if action is not None:
        stmt = stmt.where(AuditEntry.action == action)
    if cursor_seq is not None:
        stmt = stmt.where(AuditEntry.seq < cursor_seq)
    stmt = stmt.order_by(AuditEntry.seq.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    return _split_page(rows, limit)


def _split_page(rows: list[AuditEntry], limit: int) -> tuple[list[AuditEntry], int | None]:
    """Trim an over-fetched (limit+1) result to one page + the next cursor seq."""
    if len(rows) > limit:
        page = rows[:limit]
        return page, page[-1].seq
    return rows, None


async def iter_chain_ordered(db: AsyncSession) -> AsyncIterator[AuditEntry]:
    """Stream every entry in ascending ``seq`` order — the verifier's input.

    Streamed (server-side cursor on Postgres) so verifying a long chain does not
    load the whole table into memory. ``populate_existing`` forces each row to reflect
    authoritative DB state rather than any cached identity-map instance — a verifier
    must hash what is actually stored, not a stale in-session copy.
    """
    stmt = (
        select(AuditEntry).order_by(AuditEntry.seq.asc()).execution_options(populate_existing=True)
    )
    result = await db.stream(stmt)
    async for entry in result.scalars():
        yield entry
