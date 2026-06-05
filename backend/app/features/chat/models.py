"""SQLAlchemy ORM model for the chat feature: ChatMessage (Slice 11).

One private conversation per ``(engagement_id, user_id)`` pair, linear and
oldest-first. There is intentionally no ``conversations`` table in this slice — a
conversation is implicitly the set of rows sharing the same
``(engagement_id, user_id)`` (slice Open Question 2; a conversations table lands
with the deferred §5.4 reset/fork/branch feature).

NO columns are added to any other table. ``user_id`` here is NOT provenance bolted
onto a shared entity (§8.2 / §17.4 anti-pattern): ``chat_messages`` *is* the per-user
chat table, so ownership is its primary key concept. AI-call attribution lives only
in ``audit_entries`` (the §14 record), never on this row.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# Canonical DB-level vocabularies. The Pydantic StrEnums in schemas.py mirror these
# and are guarded against drift by test_schemas.
CHAT_ROLES: tuple[str, ...] = ("user", "assistant")
CHAT_STATUSES: tuple[str, ...] = ("complete", "pending", "failed")

# JSONB on Postgres; generic JSON on the SQLite test engine (same variant pattern as the
# graph models' _PROPS_JSON) so the in-memory unit-test DDL renders.
_GRAPH_CONTEXT_JSON = JSONB().with_variant(JSON(), "sqlite")

_ROLE_CHECK_SQL = "role IN (" + ", ".join(f"'{r}'" for r in CHAT_ROLES) + ")"
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{s}'" for s in CHAT_STATUSES) + ")"


class ChatMessage(Base):
    """One message in a user's private chat for an engagement.

    ``user`` rows are always ``complete`` and carry the verbatim user text. An
    ``assistant`` row is inserted ``pending`` with empty ``content`` alongside the
    user row (persist-first), then transitions to ``complete`` (final streamed text)
    or ``failed`` (Ollama unreachable) when the stream finishes.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK_SQL, name="ck_chat_messages_role"),
        CheckConstraint(_STATUS_CHECK_SQL, name="ck_chat_messages_status"),
        # The load-bearing access path: per-user conversation read (oldest-first
        # paging) and the recent-window prompt fetch both scan this index.
        Index(
            "ix_chat_messages_engagement_user_created",
            "engagement_id",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'complete'")
    )
    # The Ollama model name for assistant rows (audit/debug); null for user rows.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Raw counts from Ollama if returned — stored for a future §14/Slice-36 surface,
    # not rendered in this slice.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The per-turn AI debug record (§14, Slice 12): the resolved §5.3 relevant subset
    # (nodes with inclusion reasons + edges), the rendered context_block, and the raw_prompt
    # sent to the model. Set on an ASSISTANT row — first as transient client inputs at POST
    # time, then overwritten with the canonical subset at finalize (Decision 4). NULL for
    # user rows and for any assistant row completed before this slice. This is debug data on
    # the turn that USED the graph — NOT a provenance column on a graph entity (§8.2).
    graph_context: Mapped[dict[str, Any] | None] = mapped_column(_GRAPH_CONTEXT_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
