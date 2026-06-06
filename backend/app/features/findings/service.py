"""Business logic (orchestration + invariants) for the findings feature (Slice 19).

Single-writer NON-routing (Decision 1 / ADR-0001):
  Findings are an ORDINARY feature table. Unlike the graph (which serializes every
  write through the per-engagement single-writer process), findings are written
  directly in the request session. This is deliberate: a finding has its own
  verification/remediation lifecycle that the graph does not, and there is no
  cross-finding consistency invariant the single writer would protect. Findings
  never touch graph_nodes/graph_edges rows — they only *reference* a node by FK —
  so the CLAUDE.md "don't write to the graph outside the single-writer process"
  anti-pattern is not in play. See Risk 1 / Decision 1 in the slice spec.

Domain exceptions raised here are translated to HTTP codes in router.py via the
core error-handler registry (Starlette MRO-based lookup):

  EngagementNotFound  → NotFoundError → 404
  FindingNotFound     → NotFoundError → 404
  LinkedNodeNotFound  → NotFoundError → 404
  EngagementArchived  → ConflictError → 409

Membership chokepoint (§17.1 / §4 no-admin-bypass):
  Every public function first calls _require_member(), which delegates to
  engagements.repository.get_engagement_for_member(). Both "engagement missing"
  and "caller not a member" collapse to EngagementNotFound (→404) so a non-member
  cannot infer the engagement exists. Admin role is never consulted (§4).

Archived guard (§4 read-only):
  _require_writable() raises EngagementArchived (→409) on every WRITE path when
  the engagement's status is "archived". READ paths (list_findings, get_finding)
  skip it so archived data stays accessible.

Audit (§14):
  Every mutation emits exactly one attributed, hash-chained audit entry with the
  action mapped from the mutation kind, committed ATOMICALLY with the state
  change (and, for non-create mutations, the FindingHistory pre-state snapshot).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement
from app.features.findings import repository as repo
from app.features.findings.errors import (
    EngagementArchived,
    EngagementNotFound,
    FindingNotFound,
    LinkedNodeNotFound,
)
from app.features.findings.models import Finding as FindingRow
from app.features.findings.schemas import (
    Finding,
    FindingCreate,
    FindingList,
    FindingUpdate,
    RemediationUpdate,
    VerificationUpdate,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _require_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> Engagement:
    """Fused existence + membership check — the §17.1 isolation chokepoint.

    Returns the Engagement (caller may inspect .status for the archived guard
    without an extra round-trip).

    Raises:
        EngagementNotFound: engagement missing OR caller not a member (→404;
                            admin role ignored, §4).
    """
    pair = await eng_repo.get_engagement_for_member(db, engagement_id, user_id)
    if pair is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")
    engagement, _ = pair
    return engagement


def _require_writable(engagement: Engagement) -> None:
    """Raise EngagementArchived (→409) if the engagement is archived (§4).

    Called on every WRITE path. Read paths skip it so archived findings remain
    accessible for inspection / report rendering later.
    """
    if engagement.status == "archived":
        raise EngagementArchived(f"Engagement {engagement.id} is archived; writes are not allowed")


async def _validate_node_link(
    db: AsyncSession,
    engagement_id: UUID,
    node_id: UUID | None,
) -> None:
    """Validate an optional node link points to a live node in THIS engagement.

    A None node_id is always valid (no link / unlink). A non-null node_id that is
    missing, soft-deleted, or in another engagement raises LinkedNodeNotFound
    (→404 with a message that never discloses cross-engagement existence, §17.1).
    """
    if node_id is None:
        return
    if not await repo.node_exists_in_engagement(db, engagement_id, node_id):
        raise LinkedNodeNotFound(f"Node {node_id} not found in this engagement")


async def _get_owned_finding(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
) -> FindingRow:
    """Fetch an engagement-scoped finding or raise FindingNotFound (→404)."""
    finding = await repo.get_finding(db, engagement_id, finding_id)
    if finding is None:
        raise FindingNotFound(f"Finding {finding_id} not found")
    return finding


def _to_schema(row: FindingRow) -> Finding:
    """Map an ORM Finding row to the API schema (expire_on_commit=False keeps the
    attributes loaded after commit, so this is safe post-commit)."""
    return Finding.model_validate(row)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_findings(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
    include_deleted: bool = False,
) -> FindingList:
    """Return the engagement's findings, newest-first (READ — no archived guard).

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
    """
    await _require_member(db, engagement_id, user_id)
    rows = await repo.list_findings(db, engagement_id, include_deleted=include_deleted)
    return FindingList(items=[_to_schema(r) for r in rows])


async def get_finding(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
    user_id: UUID,
) -> Finding:
    """Return one finding's detail (READ — no archived guard).

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        FindingNotFound:    finding missing or in another engagement (→404).
    """
    await _require_member(db, engagement_id, user_id)
    finding = await _get_owned_finding(db, engagement_id, finding_id)
    return _to_schema(finding)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def create_finding(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
    payload: FindingCreate,
) -> Finding:
    """Create a finding with default lifecycle states (unverified/open).

    Emits one ``finding_created`` audit entry, committed atomically. No history
    snapshot on create — there is no prior state to revert to.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
        LinkedNodeNotFound: node_id given but not a live node here (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    await _validate_node_link(db, engagement_id, payload.node_id)

    finding = await repo.insert_finding(
        db,
        engagement_id=engagement_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity.value,
        node_id=payload.node_id,
    )
    await audit_service.record(
        db,
        action=AuditAction.FINDING_CREATED,
        actor_user_id=user_id,
        engagement_id=engagement_id,
        target_type="finding",
        target_id=str(finding.id),
    )
    await db.commit()
    return _to_schema(finding)


async def update_finding(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
    user_id: UUID,
    payload: FindingUpdate,
) -> Finding:
    """Update a finding's title/description/severity and/or node link.

    Only the fields the caller explicitly sent are applied (``model_fields_set``);
    an explicit ``node_id: null`` unlinks while an omitted node_id is left alone
    (Risk 4). Records a pre-mutation history snapshot, then emits one
    ``finding_updated`` audit entry, all committed atomically.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
        FindingNotFound:    finding missing or in another engagement (→404).
        LinkedNodeNotFound: a non-null node_id is not a live node here (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    finding = await _get_owned_finding(db, engagement_id, finding_id)

    fields: dict[str, object] = {}
    # Non-nullable columns: applied only when present AND non-null (the UI never
    # sends an explicit null for these; the contract types them non-nullable).
    if payload.title is not None:
        fields["title"] = payload.title
    if payload.description is not None:
        fields["description"] = payload.description
    if payload.severity is not None:
        fields["severity"] = payload.severity.value
    # node_id is nullable: presence in model_fields_set means apply (null = unlink).
    if "node_id" in payload.model_fields_set:
        await _validate_node_link(db, engagement_id, payload.node_id)
        fields["node_id"] = payload.node_id

    await repo.record_finding_history(db, finding=finding)
    finding = await repo.update_finding_row(db, finding=finding, fields=fields)
    await audit_service.record(
        db,
        action=AuditAction.FINDING_UPDATED,
        actor_user_id=user_id,
        engagement_id=engagement_id,
        target_type="finding",
        target_id=str(finding.id),
    )
    await db.commit()
    return _to_schema(finding)


async def set_verification(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
    user_id: UUID,
    payload: VerificationUpdate,
) -> Finding:
    """Set verification status (free transition — Decision 3).

    Records a pre-mutation history snapshot, emits one
    ``finding_verification_changed`` audit entry, committed atomically.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
        FindingNotFound:    finding missing or in another engagement (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    finding = await _get_owned_finding(db, engagement_id, finding_id)

    await repo.record_finding_history(db, finding=finding)
    finding = await repo.set_verification(
        db, finding=finding, status=payload.verification_status.value
    )
    await audit_service.record(
        db,
        action=AuditAction.FINDING_VERIFICATION_CHANGED,
        actor_user_id=user_id,
        engagement_id=engagement_id,
        target_type="finding",
        target_id=str(finding.id),
    )
    await db.commit()
    return _to_schema(finding)


async def set_remediation(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
    user_id: UUID,
    payload: RemediationUpdate,
) -> Finding:
    """Set remediation status (free transition — Decision 3; stays usable for the
    Slice 25 retest workflow).

    Records a pre-mutation history snapshot, emits one
    ``finding_remediation_changed`` audit entry, committed atomically.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
        FindingNotFound:    finding missing or in another engagement (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    finding = await _get_owned_finding(db, engagement_id, finding_id)

    await repo.record_finding_history(db, finding=finding)
    finding = await repo.set_remediation(
        db, finding=finding, status=payload.remediation_status.value
    )
    await audit_service.record(
        db,
        action=AuditAction.FINDING_REMEDIATION_CHANGED,
        actor_user_id=user_id,
        engagement_id=engagement_id,
        target_type="finding",
        target_id=str(finding.id),
    )
    await db.commit()
    return _to_schema(finding)


async def delete_finding(
    db: AsyncSession,
    engagement_id: UUID,
    finding_id: UUID,
    user_id: UUID,
) -> None:
    """Soft-delete a finding (recoverable via history).

    Records a pre-mutation history snapshot (the state a revert would restore),
    then emits one ``finding_deleted`` audit entry, committed atomically.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
        FindingNotFound:    finding missing or in another engagement (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    finding = await _get_owned_finding(db, engagement_id, finding_id)

    await repo.record_finding_history(db, finding=finding)
    await repo.soft_delete_finding(db, finding=finding)
    await audit_service.record(
        db,
        action=AuditAction.FINDING_DELETED,
        actor_user_id=user_id,
        engagement_id=engagement_id,
        target_type="finding",
        target_id=str(finding_id),
    )
    await db.commit()
