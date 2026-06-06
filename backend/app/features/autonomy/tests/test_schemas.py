"""Unit tests for autonomy schemas + the delegable-reason vocabulary (Slice 18)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.features.approvals.schemas import ApprovalReason
from app.features.autonomy.models import DELEGABLE_REASONS
from app.features.autonomy.schemas import AutonomyGrantCreate


def test_delegable_reasons_match_approval_reasons_minus_unclassified() -> None:
    """Drift guard: DELEGABLE_REASONS is exactly every ApprovalReason except the
    never-delegable unclassified_manifest fail-safe."""
    expected = {r.value for r in ApprovalReason} - {ApprovalReason.UNCLASSIFIED_MANIFEST.value}
    assert set(DELEGABLE_REASONS) == expected


@pytest.mark.parametrize(
    "reason",
    [
        ApprovalReason.TARGET_WRITE,
        ApprovalReason.AGGRESSIVE_SCAN,
        ApprovalReason.CREDENTIAL_ATTACK,
        ApprovalReason.OUT_OF_SCOPE,
    ],
)
def test_create_accepts_delegable_reasons(reason: ApprovalReason) -> None:
    body = AutonomyGrantCreate(reason=reason)
    assert body.reason is reason


def test_create_rejects_unclassified_manifest() -> None:
    with pytest.raises(ValidationError):
        AutonomyGrantCreate(reason=ApprovalReason.UNCLASSIFIED_MANIFEST)


def test_create_rejects_unknown_reason() -> None:
    bad: Any = "totally_made_up"  # Any so both mypy configs accept the deliberate bad value
    with pytest.raises(ValidationError):
        AutonomyGrantCreate(reason=bad)
