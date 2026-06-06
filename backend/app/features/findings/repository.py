"""Database access for the findings feature: async Postgres CRUD for findings and
the finding_history table (Slice 19 task 5).

All functions are module-level async, accept an AsyncSession first, and follow the
project pattern: select()/execute() for reads, flush()+refresh() for
server-generated defaults. The caller (service) owns the transaction commit.

Findings are an ordinary feature table — they do NOT route through the
single-writer process (Decision 1, see service.py). ``node_exists_in_engagement``
is a read-only cross-feature query against ``graph_nodes`` that validates a link
target is a live node in the SAME engagement before the FK is set (§8.1/§17.1).

NOTE (resolved decision D2): finding_history snapshots are written, but this slice
adds NO finding ``/undo`` endpoint — a non-authorship-aware revert would let one
engagement member silently clobber another's edit. Finding revert must arrive
later as an authorship-aware revert (Slice 09 pattern, feeding Slice 25 retest +
Slice 33 replay). Persisting history now makes that cheap.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.findings.models import Finding, FindingHistory
from app.features.graph.models import GraphNode

# ---------------------------------------------------------------------------
# Findings — writes
# ---------------------------------------------------------------------------


async def insert_finding(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    title: str,
    description: str,
    severity: str,
    node_id: UUID | None,
) -> Finding:
    """Insert a new Finding row with default lifecycle states (unverified/open).

    flush()+refresh() populate server-generated defaults (id, verification_status,
    remediation_status, deleted, created_at, updated_at) before returning. The
    caller commits.
    """
    finding = Finding(
        engagement_id=engagement_id,
        title=title,
        description=description,
        severity=severity,
        node_id=node_id,
    )
    db.add(finding)
    await db.flush()
    await db.refresh(finding)
    return finding


async def update_finding_row(
    db: AsyncSession,
    *,
    finding: Finding,
    fields: dict[str, Any],
) -> Finding:
    """Apply a resolved set of column→value updates to an existing Finding.

    ``fields`` contains ONLY the columns the caller actually wants to change
    (the service builds it from ``model_fields_set`` so an explicit ``node_id:
    null`` unlink is distinct from an omitted node_id — Risk 4). Does NOT record
    history — call ``record_finding_history`` before this. The caller commits.
    """
    for name, value in fields.items():
        setattr(finding, name, value)
    await db.flush()
    await db.refresh(finding)
    return finding


async def set_verification(
    db: AsyncSession,
    *,
    finding: Finding,
    status: str,
) -> Finding:
    """Set the finding's verification_status (free transition — Decision 3)."""
    finding.verification_status = status
    await db.flush()
    await db.refresh(finding)
    return finding


async def set_remediation(
    db: AsyncSession,
    *,
    finding: Finding,
    status: str,
) -> Finding:
    """Set the finding's remediation_status (free transition — Decision 3)."""
    finding.remediation_status = status
    await db.flush()
    await db.refresh(finding)
    return finding


async def soft_delete_finding(
    db: AsyncSession,
    *,
    finding: Finding,
) -> None:
    """Soft-delete a finding (set deleted=True). Recoverable via history. Does NOT
    record history — the service records the pre-state snapshot first. Caller commits.
    """
    finding.deleted = True
    await db.flush()


async def record_finding_history(
    db: AsyncSession,
    *,
    finding: Finding,
) -> FindingHistory:
    """Append a FindingHistory row capturing the finding's CURRENT state.

    Call this BEFORE mutating the finding so the snapshot captures the pre-mutation
    state a revert would restore (§8.2). flush()+refresh() populate the
    server-generated id and recorded_at. The caller commits.
    """
    history = FindingHistory(
        engagement_id=finding.engagement_id,
        finding_id=finding.id,
        title=finding.title,
        description=finding.description,
        severity=finding.severity,
        verification_status=finding.verification_status,
        remediation_status=finding.remediation_status,
        node_id=finding.node_id,
        deleted=finding.deleted,
    )
    db.add(history)
    await db.flush()
    await db.refresh(history)
    return history


# ---------------------------------------------------------------------------
# Findings — reads
# ---------------------------------------------------------------------------


async def get_finding(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
) -> Finding | None:
    """Return the engagement-scoped finding by id, or None.

    Scoped to ``engagement_id`` so a finding in another engagement is never
    returned (§17.1). Returns the row regardless of the ``deleted`` flag — the
    service decides how to treat a soft-deleted finding.
    """
    result = await db.execute(
        select(Finding).where(
            Finding.id == finding_id,
            Finding.engagement_id == engagement_id,
        )
    )
    return result.scalar_one_or_none()


async def list_findings(
    db: AsyncSession,
    engagement_id: UUID,
    include_deleted: bool = False,
) -> list[Finding]:
    """Return the engagement's findings, newest-first.

    Excludes soft-deleted findings unless ``include_deleted`` is True. Ordered by
    (created_at DESC, id DESC) — the id tiebreaker keeps ordering deterministic on
    coarse clocks (SQLite tests).
    """
    stmt = select(Finding).where(Finding.engagement_id == engagement_id)
    if not include_deleted:
        stmt = stmt.where(Finding.deleted.is_(False))
    stmt = stmt.order_by(desc(Finding.created_at), desc(Finding.id))
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Cross-feature link validation
# ---------------------------------------------------------------------------


async def node_exists_in_engagement(
    db: AsyncSession,
    engagement_id: UUID,
    node_id: UUID,
) -> bool:
    """Return True iff ``node_id`` is a LIVE GraphNode in THIS engagement.

    Read-only cross-feature query (§8.1 link validation). Scoped to
    ``engagement_id`` and ``deleted = false`` so a finding can only link to a live
    node in its own engagement; a missing, soft-deleted, or cross-engagement node
    returns False, which the service turns into a 404 that does not disclose
    cross-engagement existence (§17.1).
    """
    result = await db.execute(
        select(GraphNode.id).where(
            GraphNode.id == node_id,
            GraphNode.engagement_id == engagement_id,
            GraphNode.deleted.is_(False),
        )
    )
    return result.scalar_one_or_none() is not None
