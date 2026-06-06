"""Pydantic v2 request/response models for the findings feature (Slice 19).

Schemas match the Slice 19 OpenAPI contract exactly — field names, types, enums,
and validation constraints are authoritative here.

Wire format: the enum values are snake_case (``false_positive``, ``risk_accepted``)
to match the Pydantic StrEnum members; the UI maps them to display labels.
``severity`` on ``FindingCreate`` is **required** (no literal default) so it does
not become an awkward forced field in the generated client (see project memory
"OpenAPI literal-default → required TS field").
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

# Max description length (characters) — matches the contract's ``maxLength: 65536``.
_DESCRIPTION_MAX_LEN = 64 * 1024  # 64 KB


# ---------------------------------------------------------------------------
# Enums (snake_case wire values)
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    """Simple primary classification (§9.1). The single required classification;
    advanced classifications (CVSS/OWASP/ATT&CK) are deferred to Slice 20."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class VerificationStatus(StrEnum):
    """Verification lifecycle (§9.2). Defaults to ``unverified`` on create.
    Free transitions — no enforced state machine (Decision 3)."""

    unverified = "unverified"
    verified = "verified"
    false_positive = "false_positive"


class RemediationStatus(StrEnum):
    """Remediation lifecycle (§9.2). Defaults to ``open`` on create. Stays mutable
    for the retest workflow (Slice 25); free transitions (Decision 3)."""

    open = "open"
    fixed = "fixed"
    risk_accepted = "risk_accepted"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class FindingCreate(BaseModel):
    """Request body for POST .../findings.

    ``severity`` is required (no default) so the generated client never forces it
    awkwardly. ``description`` defaults to "" and ``node_id`` is optional — a
    finding can exist before its graph node is mapped (§8.1)."""

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=_DESCRIPTION_MAX_LEN)
    severity: Severity
    node_id: UUID | None = None


class FindingUpdate(BaseModel):
    """Request body for PATCH .../findings/{finding_id}.

    At least one field must be present. ``node_id`` distinguishes three cases via
    ``model_fields_set`` (Risk 4):

      * omitted          → leave the link unchanged
      * ``"node_id": null``  → unlink (set the FK to NULL)
      * ``"node_id": <uuid>``→ relink (validated against the engagement's nodes)

    The service inspects ``model_fields_set`` to apply only the fields the caller
    actually sent, so a ``null`` node_id is never confused with an omitted one."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=_DESCRIPTION_MAX_LEN)
    severity: Severity | None = None
    node_id: UUID | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "FindingUpdate":
        # model_fields_set captures the fields the client explicitly sent, so an
        # explicit ``node_id: null`` counts as "present" while an omitted field does not.
        # PydanticCustomError (not a bare ValueError) keeps the error's ctx free of a
        # raw exception object, so the core 422 handler can JSON-serialize exc.errors().
        if not self.model_fields_set:
            raise PydanticCustomError("at_least_one_field", "At least one field must be provided.")
        return self


class VerificationUpdate(BaseModel):
    """Request body for PATCH .../findings/{finding_id}/verification."""

    verification_status: VerificationStatus


class RemediationUpdate(BaseModel):
    """Request body for PATCH .../findings/{finding_id}/remediation."""

    remediation_status: RemediationStatus


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """Response model for a finding (maps from the ORM row, ``from_attributes``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    title: str
    description: str
    severity: Severity
    verification_status: VerificationStatus
    remediation_status: RemediationStatus
    node_id: UUID | None
    deleted: bool
    created_at: datetime
    updated_at: datetime


class FindingList(BaseModel):
    """A list of findings (newest-first)."""

    items: list[Finding]
