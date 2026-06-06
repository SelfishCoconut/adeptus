"""SQLAlchemy ORM models for the audit feature: AuditEntry, AuditChainHead.

The audit log is an append-only, hash-chained, tamper-evident record (§14). No
columns are added to any other table — attribution lives ONLY here (§8.2 / §17.4):
``actor_user_id`` / ``engagement_id`` are denormalized, immutable, **hashed** UUIDs
with *no* foreign key, so deleting a user or engagement can never touch (or break)
an audit row, while a SQL rewrite of either field is still caught by the verifier
(Slice 10 Open Question 2, RESOLVED).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base

# JSONB on Postgres (production + migrations); generic JSON on SQLite so the
# in-memory unit-test engine can render the DDL. Same idiom as graph/mcp models.
_PAYLOAD_JSON = JSONB().with_variant(JSON(), "sqlite")

# Canonical, DB-level source of truth for the audit action vocabulary. The
# Pydantic ``AuditAction`` StrEnum in schemas.py is checked against this tuple
# (test_schemas) so the enum and the CHECK constraint can never silently drift.
# Reserved actions (no caller in Slice 10) are included so Slices 11/16 need no
# migration: ``approval_granted`` / ``approval_rejected`` (Slice 16, with
# ``self_approved``) and ``ai_call`` (Slice 11+).
AUDIT_ACTIONS: tuple[str, ...] = (
    "login",
    "logout",
    "login_failed",
    "tool_run",
    "tool_run_completed",
    "graph_node_created",
    "graph_node_updated",
    "graph_node_deleted",
    "graph_edge_created",
    "graph_edge_deleted",
    "approval_granted",
    "approval_rejected",
    "ai_call",
    # Slice 18 — standing autonomy (delegation pattern, §5.2).
    "approval_auto_granted",
    "autonomy_granted",
    "autonomy_revoked",
    # Slice 19 — findings model + lifecycle (§9.1/§9.2/§14).
    "finding_created",
    "finding_updated",
    "finding_verification_changed",
    "finding_remediation_changed",
    "finding_deleted",
)

# 64 hex zeros — the genesis ``prev_hash`` and the empty-chain head pointer.
GENESIS_HASH: str = "0" * 64

_ACTION_CHECK_SQL = "action IN (" + ", ".join(f"'{a}'" for a in AUDIT_ACTIONS) + ")"


class AuditEntry(Base):
    """One append-only, hash-chained audit record.

    ``entry_hash = SHA-256(prev_hash_bytes || canonical(content_fields))`` where the
    content fields are ``seq``, ``created_at``, ``action``, ``actor_user_id``,
    ``engagement_id``, ``target_type``, ``target_id``, ``self_approved`` and ``payload``
    (see ``hashing.compute_entry_hash`` — the single source of truth for writer and
    verifier). There is intentionally no ``updated_at``, no soft-delete, and no
    update/delete path: the table is append-only by design.
    """

    __tablename__ = "audit_entries"
    __table_args__ = (
        CheckConstraint(_ACTION_CHECK_SQL, name="ck_audit_entries_action"),
        UniqueConstraint("seq", name="uq_audit_entries_seq"),
        UniqueConstraint("entry_hash", name="uq_audit_entries_entry_hash"),
        # Engagement-scoped newest-first paging (backward index scan on seq DESC).
        Index("ix_audit_entries_engagement_seq", "engagement_id", text("seq DESC")),
        # Action filter + global newest-first paging.
        Index("ix_audit_entries_action_seq", "action", text("seq DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Gap-free monotonic chain position, assigned under the audit_chain_head lock
    # (NOT a bare SERIAL, which gaps on rollback). UNIQUE so a fork hard-fails at the DB.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Denormalized, immutable, hashed — no FK (see module docstring). NULL only for
    # system/anonymous events (e.g. login_failed).
    actor_user_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Denormalized, immutable, hashed — no FK. NULL for instance-global events.
    engagement_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # §5.2 — NULL except on approval_granted/approval_rejected (populated by Slice 16).
    self_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        _PAYLOAD_JSON, nullable=False, server_default=text("'{}'")
    )
    # Hex SHA-256 of the previous row's entry_hash; genesis == 64 zeros.
    prev_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    # Hex SHA-256 over prev_hash || canonical(content). UNIQUE (see table args).
    entry_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditChainHead(Base):
    """Single-row lock + head pointer that serializes appends (the single-appender
    invariant — the audit analogue of ADR-0001's single-writer).

    Appends ``SELECT ... FOR UPDATE`` this row inside the insert transaction, so
    ``seq``/``prev_hash`` are assigned under a strict total order and the chain
    cannot fork under concurrency. The CHECK ``id = 1`` enforces exactly one row.
    """

    __tablename__ = "audit_chain_head"
    __table_args__ = (CheckConstraint("id = 1", name="ck_audit_chain_head_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    head_hash: Mapped[str] = mapped_column(
        CHAR(64), nullable=False, server_default=text(f"'{GENESIS_HASH}'")
    )
