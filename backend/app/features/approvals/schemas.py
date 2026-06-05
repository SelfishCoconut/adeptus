"""Pydantic v2 schemas for the approvals feature (Slice 16).

Two groups:

* **Contract schemas** exposed over HTTP (mirrored 1:1 by the OpenAPI delta):
  ``ApprovalStatus`` / ``ApprovalReason`` enums, ``ApprovalRequestRead``,
  ``ApprovalRequestPage``, ``ApprovalConflict``.
* **Internal value objects** the chat→classify→gate pipeline passes around
  (NOT persisted, NOT all in OpenAPI): ``ApprovalTier``, ``ProposedAction``,
  ``ClassificationResult``, ``AutonomousAction``. ``AutonomousAction`` is mirrored in
  the frontend's hand-written WebSocket-frame contract, not the OpenAPI client.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ApprovalConflict",
    "ApprovalReason",
    "ApprovalRequestPage",
    "ApprovalRequestRead",
    "ApprovalStatus",
    "ApprovalTier",
    "AutonomousAction",
    "ClassificationResult",
    "ProposedAction",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ApprovalStatus(StrEnum):
    """Request lifecycle (§5.2). Mirrors ``models.APPROVAL_STATUSES`` exactly —
    guarded by ``test_schemas.test_status_enum_matches_db_vocabulary`` so the enum and
    the DB CHECK constraint can never silently drift. No expiry: approvals do not
    time out — a request stays ``PENDING`` until a member acts.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalReason(StrEnum):
    """Why a command was gated (§5.2 dangerous categories) plus ``unclassified_manifest``.

    ``TARGET_WRITE`` / ``AGGRESSIVE_SCAN`` / ``CREDENTIAL_ATTACK`` are the three §5.2
    dangerous categories computed in this slice. ``UNCLASSIFIED_MANIFEST`` is the
    fail-safe reason for a tool whose manifest carries no weight and no capability flags
    (Resolved decision 2 escape hatch). ``OUT_OF_SCOPE`` is **reserved for Slice 17**
    (soft scope enforcement) and is never produced in this slice.
    """

    TARGET_WRITE = "target_write"
    AGGRESSIVE_SCAN = "aggressive_scan"
    CREDENTIAL_ATTACK = "credential_attack"
    UNCLASSIFIED_MANIFEST = "unclassified_manifest"
    OUT_OF_SCOPE = "out_of_scope"  # reserved — Slice 17, never returned here


class ApprovalTier(StrEnum):
    """The two-tier risk classification (§5.2). Internal — an ``AUTONOMOUS`` command
    runs immediately and never creates an approval request; ``REQUIRES_APPROVAL`` gates.
    """

    AUTONOMOUS = "autonomous"
    REQUIRES_APPROVAL = "requires_approval"


# ---------------------------------------------------------------------------
# Contract (HTTP) schemas
# ---------------------------------------------------------------------------


class ApprovalRequestRead(BaseModel):
    """One approval request as exposed by the read/decision API.

    ``acted_by_username`` is resolved at read time (not a DB column) and set as a
    transient attribute on the ORM row by the service before ``model_validate``; it
    defaults to ``None`` so pending rows (no decider yet) validate cleanly.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    chat_message_id: UUID
    initiator_user_id: UUID
    server_name: str
    tool_name: str
    args: dict[str, Any]
    preset_name: str | None = None
    rationale: str | None = None
    reasons: list[ApprovalReason]
    status: ApprovalStatus
    acted_by_user_id: UUID | None = None
    acted_by_username: str | None = None
    self_approved: bool | None = None
    tool_run_id: UUID | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalRequestPage(BaseModel):
    """A page of approval requests with an opaque cursor for the next (older) page."""

    items: list[ApprovalRequestRead]
    next_cursor: str | None


class ApprovalConflict(BaseModel):
    """409 body distinguishing the two conflict cases on a decision endpoint.

    ``already_decided`` — the request is already terminal (double-/concurrent-decision
    guard); ``status`` carries its current terminal status. ``engagement_archived`` —
    approve-and-run is blocked in an archived engagement (§4); ``status`` is omitted.
    """

    reason: Literal["already_decided", "engagement_archived"]
    status: ApprovalStatus | None = None


# ---------------------------------------------------------------------------
# Internal value objects (not persisted; not all in OpenAPI)
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    """A single command the AI proposed this turn — the normalized form both the native
    tool-call path and the instructed-block fallback produce before classification.
    """

    server_name: str
    tool_name: str
    args: dict[str, Any]
    preset_name: str | None = None
    rationale: str | None = None


class ClassificationResult(BaseModel):
    """The classifier's verdict for one ``ProposedAction`` (§5.2). ``reasons`` is empty
    for ``AUTONOMOUS`` and non-empty for ``REQUIRES_APPROVAL``.
    """

    tier: ApprovalTier
    reasons: list[ApprovalReason] = []


class AutonomousAction(BaseModel):
    """The lightweight "running automatically" card payload for the no-gate path —
    no DB row is created. Rides the chat WebSocket ``proposed_action`` frame, which is
    outside OpenAPI, so it is **hand-mirrored** in the frontend at
    ``frontend/src/features/approvals/api.ts`` (interface ``AutonomousAction``) — keep the
    two in sync when changing this shape.
    """

    server_name: str
    tool_name: str
    args: dict[str, Any]
    preset_name: str | None = None
    rationale: str | None = None
    tool_run_id: UUID
