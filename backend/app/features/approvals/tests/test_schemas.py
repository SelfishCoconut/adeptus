"""Unit tests for approvals schemas (Slice 16 task 2)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.features.approvals.models import APPROVAL_STATUSES
from app.features.approvals.schemas import (
    ApprovalConflict,
    ApprovalReason,
    ApprovalRequestPage,
    ApprovalRequestRead,
    ApprovalStatus,
    ApprovalTier,
    ClassificationResult,
)


def test_status_enum_matches_db_vocabulary() -> None:
    # The StrEnum and the DB CHECK-constraint tuple must never drift (same values, order).
    assert tuple(s.value for s in ApprovalStatus) == APPROVAL_STATUSES


def test_reason_enum_includes_unclassified_manifest_escape_hatch() -> None:
    assert ApprovalReason.UNCLASSIFIED_MANIFEST.value == "unclassified_manifest"


def test_reason_enum_reserves_out_of_scope_for_slice_17() -> None:
    # Present in the vocabulary (so no migration later) but never produced this slice.
    assert ApprovalReason.OUT_OF_SCOPE.value == "out_of_scope"


def test_reason_enum_covers_the_three_dangerous_categories() -> None:
    dangerous = {
        ApprovalReason.TARGET_WRITE,
        ApprovalReason.AGGRESSIVE_SCAN,
        ApprovalReason.CREDENTIAL_ATTACK,
    }
    assert dangerous <= set(ApprovalReason)


def test_tier_enum_values() -> None:
    assert ApprovalTier.AUTONOMOUS.value == "autonomous"
    assert ApprovalTier.REQUIRES_APPROVAL.value == "requires_approval"


def test_autonomous_classification_has_no_reasons() -> None:
    result = ClassificationResult(tier=ApprovalTier.AUTONOMOUS)
    assert result.reasons == []


def test_gated_classification_carries_nonempty_reasons() -> None:
    result = ClassificationResult(
        tier=ApprovalTier.REQUIRES_APPROVAL,
        reasons=[ApprovalReason.CREDENTIAL_ATTACK],
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons  # non-empty for a gated request (§5.2)


class _Row:
    """Stand-in for an ORM ApprovalRequest (exercises from_attributes)."""

    def __init__(self, *, decided: bool) -> None:
        self.id = uuid4()
        self.engagement_id = uuid4()
        self.chat_message_id = uuid4()
        self.initiator_user_id = uuid4()
        self.server_name = "shell-exec"
        self.tool_name = "run"
        self.args = {"cmd": "hydra -l admin -P rockyou.txt ssh://10.0.0.5"}
        self.preset_name: str | None = None
        self.rationale = "Brute-force the SSH login."
        self.reasons = ["credential_attack"]
        self.created_at = datetime(2026, 6, 5, tzinfo=UTC)
        self.status = "approved" if decided else "pending"
        self.acted_by_user_id: UUID | None = uuid4() if decided else None
        self.self_approved: bool | None = True if decided else None
        self.tool_run_id: UUID | None = uuid4() if decided else None
        self.decided_at: datetime | None = datetime(2026, 6, 5, 1, tzinfo=UTC) if decided else None
        # Slice 17 scope-context columns: null on every non-out_of_scope row (the default).
        self.out_of_scope_host: str | None = None
        self.scope_checked_against: str | None = None
        # Only a decided row carries a resolved username; a pending row has NO such
        # attribute at all (the read schema's default must fill it).
        if decided:
            self.acted_by_username = "alice"


def test_read_from_pending_orm_row_without_username_attr() -> None:
    # A pending row has no acted_by_username attribute at all; the default fills it.
    row = _Row(decided=False)
    read = ApprovalRequestRead.model_validate(row)
    assert read.status is ApprovalStatus.PENDING
    assert read.reasons == [ApprovalReason.CREDENTIAL_ATTACK]
    assert read.acted_by_username is None
    assert read.self_approved is None
    # Args carried verbatim (§5.5 — no redaction).
    assert read.args == {"cmd": "hydra -l admin -P rockyou.txt ssh://10.0.0.5"}


def test_scope_context_defaults_to_none() -> None:
    # A Slice-16 row (no scope attrs at all) validates with both fields defaulting None.
    read = ApprovalRequestRead.model_validate(_Row(decided=False))
    assert read.out_of_scope_host is None
    assert read.scope_checked_against is None


def test_out_of_scope_row_carries_scope_context() -> None:
    row = _Row(decided=False)
    row.reasons = ["out_of_scope"]
    row.out_of_scope_host = "example.com"
    row.scope_checked_against = "juice-shop, 10.0.0.0/24"
    read = ApprovalRequestRead.model_validate(row)
    assert read.reasons == [ApprovalReason.OUT_OF_SCOPE]
    assert read.out_of_scope_host == "example.com"
    assert read.scope_checked_against == "juice-shop, 10.0.0.0/24"


def test_read_from_decided_orm_row_resolves_username() -> None:
    read = ApprovalRequestRead.model_validate(_Row(decided=True))
    assert read.status is ApprovalStatus.APPROVED
    assert read.acted_by_username == "alice"
    assert read.self_approved is True
    assert read.tool_run_id is not None


def test_page_shape() -> None:
    page = ApprovalRequestPage(
        items=[ApprovalRequestRead.model_validate(_Row(decided=False))],
        next_cursor="abc",
    )
    assert len(page.items) == 1
    assert page.next_cursor == "abc"
    assert ApprovalRequestPage(items=[], next_cursor=None).next_cursor is None


def test_conflict_already_decided_carries_status() -> None:
    conflict = ApprovalConflict(reason="already_decided", status=ApprovalStatus.REJECTED)
    assert conflict.reason == "already_decided"
    assert conflict.status is ApprovalStatus.REJECTED


def test_conflict_archived_omits_status() -> None:
    conflict = ApprovalConflict(reason="engagement_archived")
    assert conflict.reason == "engagement_archived"
    assert conflict.status is None
