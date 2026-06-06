"""Pydantic v2 schemas for the autonomy feature (Slice 18).

``AutonomyGrantCreate`` (request) and ``AutonomyGrantRead`` (response) are the HTTP
contract for standing-autonomy grants. The delegable ``reason`` reuses the approvals
``ApprovalReason`` enum; the create schema rejects ``unclassified_manifest`` (never
delegable — the un-manifested-tool fail-safe must always gate).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.features.approvals.schemas import ApprovalReason

__all__ = ["AutonomyGrantCreate", "AutonomyGrantRead"]


class AutonomyGrantCreate(BaseModel):
    """Request body to grant standing autonomy for one reason category."""

    reason: ApprovalReason

    @field_validator("reason")
    @classmethod
    def _reject_non_delegable(cls, value: ApprovalReason) -> ApprovalReason:
        # unclassified_manifest is the Slice-16 fail-safe for un-manifested tools; it must
        # always gate and can never be delegated. The other four reasons are delegable.
        if value is ApprovalReason.UNCLASSIFIED_MANIFEST:
            raise ValueError("unclassified_manifest is not a delegable category")
        return value


class AutonomyGrantRead(BaseModel):
    """One standing-autonomy grant as exposed by the read/grant API.

    ``granted_by_username`` is resolved at read time (not a DB column) and set as a
    transient attribute by the service before ``model_validate``; it defaults to ``None``
    so a grant whose grantor was deleted (FK SET NULL) still validates.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    reason: ApprovalReason
    granted_by_user_id: UUID | None = None
    granted_by_username: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None
