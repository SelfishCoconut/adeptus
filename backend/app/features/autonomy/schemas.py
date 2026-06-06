"""Pydantic v2 schemas for the autonomy feature (Slice 18).

``AutonomyGrantCreate`` (request) and ``AutonomyGrantRead`` (response) are the HTTP
contract for standing-autonomy grants. The delegable ``reason`` reuses the approvals
``ApprovalReason`` enum; the create schema rejects ``unclassified_manifest`` (never
delegable — the un-manifested-tool fail-safe must always gate).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.features.approvals.schemas import ApprovalReason

__all__ = ["AutonomyGrantCreate", "AutonomyGrantRead", "DelegableReason"]


class DelegableReason(StrEnum):
    """The reason categories a user may delegate standing autonomy for (§5.2).

    Exactly ``ApprovalReason`` minus ``unclassified_manifest`` (the un-manifested-tool
    fail-safe, which must always gate). Used as the request-body type so a non-delegable or
    unknown value fails standard enum validation (422) — cleanly, without a raising
    validator. Guarded against drift by ``test_schemas``.
    """

    TARGET_WRITE = "target_write"
    AGGRESSIVE_SCAN = "aggressive_scan"
    CREDENTIAL_ATTACK = "credential_attack"
    OUT_OF_SCOPE = "out_of_scope"


class AutonomyGrantCreate(BaseModel):
    """Request body to grant standing autonomy for one reason category.

    ``reason`` is a :class:`DelegableReason`, so ``unclassified_manifest`` and unknown
    values are rejected by enum validation (422) at the contract boundary.
    """

    reason: DelegableReason


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
