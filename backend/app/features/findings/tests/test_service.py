"""Service tests for the findings feature (Slice 19 task 6).

The repository and the engagement membership lookup are fully mocked (AsyncMock /
MagicMock) so these tests have no database dependency. The audit emission is
stubbed by the ``mock_audit_record`` fixture (conftest). Covers the invariants
required by the slice spec: membership 404, archived 409, node-link 404, default
statuses, the audit action per mutation kind, and history-on-mutation.
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.audit.schemas import AuditAction
from app.features.findings import service
from app.features.findings.errors import (
    EngagementArchived,
    EngagementNotFound,
    FindingNotFound,
    LinkedNodeNotFound,
)
from app.features.findings.schemas import (
    FindingCreate,
    FindingUpdate,
    RemediationStatus,
    RemediationUpdate,
    Severity,
    VerificationStatus,
    VerificationUpdate,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def _await_kwargs(mock: AsyncMock) -> Mapping[str, Any]:
    """The kwargs of a mock's last await (asserts it was awaited — narrows None)."""
    assert mock.await_args is not None
    return mock.await_args.kwargs


def _make_engagement(*, engagement_id: UUID | None = None, status: str = "active") -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
    eng.status = status
    return eng


def _make_finding(
    *,
    finding_id: UUID | None = None,
    engagement_id: UUID | None = None,
    severity: str = "high",
    verification_status: str = "unverified",
    remediation_status: str = "open",
    node_id: UUID | None = None,
    deleted: bool = False,
) -> SimpleNamespace:
    """A row-like object Finding.model_validate (from_attributes) can read."""
    return SimpleNamespace(
        id=finding_id or uuid4(),
        engagement_id=engagement_id or uuid4(),
        title="Reflected XSS on /search",
        description="",
        severity=severity,
        verification_status=verification_status,
        remediation_status=remediation_status,
        node_id=node_id,
        deleted=deleted,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def repo_mock() -> MagicMock:
    """A MagicMock standing in for the repository module, with AsyncMock methods."""
    m = MagicMock()
    for name in (
        "insert_finding",
        "get_finding",
        "list_findings",
        "update_finding_row",
        "set_verification",
        "set_remediation",
        "soft_delete_finding",
        "record_finding_history",
        "node_exists_in_engagement",
    ):
        setattr(m, name, AsyncMock())
    return m


@pytest.fixture
def patched(repo_mock: MagicMock) -> Iterator[SimpleNamespace]:
    """Patch service.repo and service.eng_repo.get_engagement_for_member."""
    member = (None, None)
    with (
        patch.object(service, "repo", repo_mock),
        patch.object(service.eng_repo, "get_engagement_for_member", new=AsyncMock()) as get_member,
    ):
        get_member.return_value = member
        yield SimpleNamespace(repo=repo_mock, get_member=get_member)


def _set_member(patched: SimpleNamespace, engagement: MagicMock) -> None:
    patched.get_member.return_value = (engagement, MagicMock())


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_defaults_unverified_open(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    patched.repo.insert_finding.return_value = _make_finding(engagement_id=eng.id)

    result = await service.create_finding(
        db, eng.id, uuid4(), FindingCreate(title="t", severity=Severity.high)
    )

    assert result.verification_status is VerificationStatus.unverified
    assert result.remediation_status is RemediationStatus.open
    mock_audit_record.assert_awaited_once()
    assert _await_kwargs(mock_audit_record)["action"] is AuditAction.FINDING_CREATED
    db.commit.assert_awaited_once()


async def test_create_non_member_404(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    patched.get_member.return_value = None
    with pytest.raises(EngagementNotFound):
        await service.create_finding(
            db, uuid4(), uuid4(), FindingCreate(title="t", severity=Severity.low)
        )
    patched.repo.insert_finding.assert_not_awaited()


async def test_create_archived_409(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement(status="archived")
    _set_member(patched, eng)
    with pytest.raises(EngagementArchived):
        await service.create_finding(
            db, eng.id, uuid4(), FindingCreate(title="t", severity=Severity.low)
        )
    patched.repo.insert_finding.assert_not_awaited()


async def test_create_with_unknown_node_404(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    patched.repo.node_exists_in_engagement.return_value = False
    with pytest.raises(LinkedNodeNotFound):
        await service.create_finding(
            db, eng.id, uuid4(), FindingCreate(title="t", severity=Severity.low, node_id=uuid4())
        )
    patched.repo.insert_finding.assert_not_awaited()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_emits_finding_updated_audit(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id)
    patched.repo.get_finding.return_value = row
    patched.repo.update_finding_row.return_value = row

    await service.update_finding(db, eng.id, row.id, uuid4(), FindingUpdate(title="new title"))

    patched.repo.record_finding_history.assert_awaited_once()
    assert _await_kwargs(mock_audit_record)["action"] is AuditAction.FINDING_UPDATED
    # Only the title was applied.
    assert _await_kwargs(patched.repo.update_finding_row)["fields"] == {"title": "new title"}


async def test_update_unknown_finding_404(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    patched.repo.get_finding.return_value = None
    with pytest.raises(FindingNotFound):
        await service.update_finding(db, eng.id, uuid4(), uuid4(), FindingUpdate(title="x"))


async def test_update_node_unlink_applies_null(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id, node_id=uuid4())
    patched.repo.get_finding.return_value = row
    patched.repo.update_finding_row.return_value = row

    # Explicit null node_id → unlink (no node validation needed for null).
    await service.update_finding(
        db, eng.id, row.id, uuid4(), FindingUpdate.model_validate({"node_id": None})
    )
    patched.repo.node_exists_in_engagement.assert_not_awaited()
    assert _await_kwargs(patched.repo.update_finding_row)["fields"] == {"node_id": None}


# ---------------------------------------------------------------------------
# verification / remediation
# ---------------------------------------------------------------------------


async def test_set_verification_verified_emits_audit(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id)
    patched.repo.get_finding.return_value = row
    patched.repo.set_verification.return_value = _make_finding(
        engagement_id=eng.id, verification_status="verified"
    )

    result = await service.set_verification(
        db,
        eng.id,
        row.id,
        uuid4(),
        VerificationUpdate(verification_status=VerificationStatus.verified),
    )
    assert result.verification_status is VerificationStatus.verified
    patched.repo.record_finding_history.assert_awaited_once()
    assert _await_kwargs(mock_audit_record)["action"] is AuditAction.FINDING_VERIFICATION_CHANGED


async def test_set_verification_false_positive(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id)
    patched.repo.get_finding.return_value = row
    patched.repo.set_verification.return_value = _make_finding(
        engagement_id=eng.id, verification_status="false_positive"
    )

    result = await service.set_verification(
        db,
        eng.id,
        row.id,
        uuid4(),
        VerificationUpdate(verification_status=VerificationStatus.false_positive),
    )
    assert result.verification_status is VerificationStatus.false_positive
    assert _await_kwargs(patched.repo.set_verification)["status"] == "false_positive"


async def test_set_remediation_risk_accepted_emits_audit(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id)
    patched.repo.get_finding.return_value = row
    patched.repo.set_remediation.return_value = _make_finding(
        engagement_id=eng.id, remediation_status="risk_accepted"
    )

    result = await service.set_remediation(
        db,
        eng.id,
        row.id,
        uuid4(),
        RemediationUpdate(remediation_status=RemediationStatus.risk_accepted),
    )
    assert result.remediation_status is RemediationStatus.risk_accepted
    patched.repo.record_finding_history.assert_awaited_once()
    assert _await_kwargs(mock_audit_record)["action"] is AuditAction.FINDING_REMEDIATION_CHANGED


async def test_set_verification_archived_409(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement(status="archived")
    _set_member(patched, eng)
    with pytest.raises(EngagementArchived):
        await service.set_verification(
            db,
            eng.id,
            uuid4(),
            uuid4(),
            VerificationUpdate(verification_status=VerificationStatus.verified),
        )
    patched.repo.set_verification.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete + reads
# ---------------------------------------------------------------------------


async def test_delete_writes_history_and_finding_deleted_audit(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement()
    _set_member(patched, eng)
    row = _make_finding(engagement_id=eng.id)
    patched.repo.get_finding.return_value = row

    await service.delete_finding(db, eng.id, row.id, uuid4())

    patched.repo.record_finding_history.assert_awaited_once()
    patched.repo.soft_delete_finding.assert_awaited_once()
    assert _await_kwargs(mock_audit_record)["action"] is AuditAction.FINDING_DELETED
    db.commit.assert_awaited_once()


async def test_read_archived_engagement_allowed(
    db: AsyncMock, patched: SimpleNamespace, mock_audit_record: AsyncMock
) -> None:
    eng = _make_engagement(status="archived")
    _set_member(patched, eng)
    patched.repo.list_findings.return_value = [_make_finding(engagement_id=eng.id)]

    # Reads must NOT raise on an archived engagement (§4 reads stay available).
    result = await service.list_findings(db, eng.id, uuid4())
    assert len(result.items) == 1
    mock_audit_record.assert_not_awaited()
