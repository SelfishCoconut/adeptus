"""Unit tests for autonomy schemas + the delegable-reason vocabulary (Slice 18)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.features.approvals.schemas import ApprovalReason
from app.features.autonomy.models import DELEGABLE_REASONS
from app.features.autonomy.schemas import AutonomyGrantCreate, DelegableReason


def test_delegable_reasons_match_approval_reasons_minus_unclassified() -> None:
    """Drift guard: DELEGABLE_REASONS (DB vocab) and DelegableReason (wire enum) are both
    exactly every ApprovalReason except the never-delegable unclassified_manifest fail-safe.
    """
    expected = {r.value for r in ApprovalReason} - {ApprovalReason.UNCLASSIFIED_MANIFEST.value}
    assert set(DELEGABLE_REASONS) == expected
    assert {r.value for r in DelegableReason} == expected


@pytest.mark.parametrize("reason", list(DelegableReason))
def test_create_accepts_delegable_reasons(reason: DelegableReason) -> None:
    body = AutonomyGrantCreate(reason=reason)
    assert body.reason is reason


def test_create_rejects_unclassified_manifest() -> None:
    bad: Any = ApprovalReason.UNCLASSIFIED_MANIFEST.value  # Any so mypy allows the bad value
    with pytest.raises(ValidationError):
        AutonomyGrantCreate(reason=bad)


def test_create_rejects_unknown_reason() -> None:
    bad: Any = "totally_made_up"  # Any so both mypy configs accept the deliberate bad value
    with pytest.raises(ValidationError):
        AutonomyGrantCreate(reason=bad)
