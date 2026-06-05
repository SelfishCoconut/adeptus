"""SQLAlchemy ORM model for the personas feature: Persona (Slice 15, §5.3 / §5.4).

One ``personas`` table holds two ownership classes, discriminated by ``is_builtin`` +
a nullable ``user_id`` (Decision 2):

  - **Built-in** rows: ``is_builtin = true``, ``user_id = NULL``, a stable ``slug``
    (``general`` / ``recon`` / ``web-exploit`` / ``report-writer``). Visible to ALL
    users, read-only, seeded idempotently at startup.
  - **Custom** rows: ``is_builtin = false``, ``user_id = <creator>``, ``slug = NULL``.
    Visible to / editable / deletable by ONLY their creator (§5.4 / §17.1).

``user_id`` here is NOT provenance bolted onto a shared entity (§8.2 / §17.4 anti-
pattern): ``personas`` *is* the per-user library, so ownership is its primary concept.
NO columns are added to any other table — the persona used for a chat turn is recorded
inside the existing ``chat_messages.graph_context`` JSONB seam, not via a FK here.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# A row is EXACTLY one of "global built-in with a slug" or "user-owned custom" — no
# half-states. Written with bare boolean predicates so the DDL renders on both Postgres
# (native boolean) and the in-memory SQLite test engine (boolean stored as 0/1).
_BUILTIN_XOR_CUSTOM_SQL = (
    "(is_builtin AND user_id IS NULL AND slug IS NOT NULL) "
    "OR (NOT is_builtin AND user_id IS NOT NULL AND slug IS NULL)"
)


class Persona(Base):
    """A named AI persona = name + distinct system prompt (§5.3).

    Either a global read-only built-in (slug set, no owner) or a user-owned custom
    persona (owner set, no slug). The ``resolve_for_turn`` chat seam reads one of these
    by id (built-in OR caller-owned) to shape a chat turn's system prompt.
    """

    __tablename__ = "personas"
    __table_args__ = (
        CheckConstraint(_BUILTIN_XOR_CUSTOM_SQL, name="ck_personas_builtin_xor_custom"),
        # One row per built-in slug; the idempotent seed upsert keys on it. Partial so
        # custom rows (NULL slug) are exempt.
        Index(
            "uq_personas_builtin_slug",
            "slug",
            unique=True,
            postgresql_where=text("is_builtin"),
        ),
        # A user cannot have two custom personas with the same name (the create/patch
        # 409). Partial so built-ins (NULL user_id) are exempt.
        Index(
            "uq_personas_user_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        # The "list my personas" read path scans the caller's own rows.
        Index("ix_personas_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # The owner for a CUSTOM persona; NULL for a built-in (shared). ON DELETE CASCADE so a
    # deleted user's custom library is cleaned up with them.
    user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Stable slug for a built-in; NULL for custom. Drives the default-persona (general)
    # lookup and the idempotent seed upsert.
    slug: Mapped[str | None] = mapped_column(String(40), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
