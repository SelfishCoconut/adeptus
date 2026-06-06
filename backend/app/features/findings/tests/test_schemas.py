"""Unit tests for findings Pydantic schemas (Slice 19 task 3)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.features.findings.schemas import (
    FindingCreate,
    FindingUpdate,
    RemediationStatus,
    Severity,
    VerificationStatus,
)


def test_enum_wire_values_are_snake_case() -> None:
    assert VerificationStatus.false_positive.value == "false_positive"
    assert RemediationStatus.risk_accepted.value == "risk_accepted"
    assert Severity.critical.value == "critical"


def test_create_defaults_description_empty_and_node_optional() -> None:
    fc = FindingCreate(title="XSS", severity=Severity.high)
    assert fc.description == ""
    assert fc.node_id is None


def test_create_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        FindingCreate(title="", severity=Severity.low)


def test_create_rejects_oversized_description() -> None:
    with pytest.raises(ValidationError):
        FindingCreate(title="t", severity=Severity.low, description="x" * (64 * 1024 + 1))


def test_update_rejects_empty_body() -> None:
    # PATCH {} → no fields set → 422.
    with pytest.raises(ValidationError):
        FindingUpdate()


def test_update_omitted_node_id_is_not_in_fields_set() -> None:
    # Omitting node_id leaves it out of model_fields_set → service leaves it unchanged.
    upd = FindingUpdate(title="new title")
    assert "node_id" not in upd.model_fields_set


def test_update_explicit_null_node_id_is_in_fields_set() -> None:
    # Explicit null → present in model_fields_set → service unlinks.
    upd = FindingUpdate.model_validate({"node_id": None})
    assert "node_id" in upd.model_fields_set
    assert upd.node_id is None


def test_update_explicit_node_id_relink() -> None:
    nid = uuid4()
    upd = FindingUpdate.model_validate({"node_id": str(nid)})
    assert "node_id" in upd.model_fields_set
    assert upd.node_id == nid


def test_update_rejects_explicit_null_on_non_nullable_fields() -> None:
    # title/description/severity are non-nullable columns: an explicit null is a 422,
    # not a silent no-op (would otherwise write a phantom history snapshot + audit entry).
    for field in ("title", "description", "severity"):
        with pytest.raises(ValidationError):
            FindingUpdate.model_validate({field: None})


def test_update_null_node_id_still_allowed() -> None:
    # node_id is the one nullable field — explicit null unlinks, must NOT be rejected.
    upd = FindingUpdate.model_validate({"node_id": None})
    assert upd.node_id is None
