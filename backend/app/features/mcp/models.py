"""SQLAlchemy ORM models for the MCP feature: ToolRun."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# JSONB on Postgres (production + migrations); generic JSON on SQLite so the
# in-memory unit-test engine can render the DDL.  Without the variant, JSONB has
# no SQLite compiler and create_all() fails for every test that builds the shared
# Base.metadata (tool_runs leaks into all features' create_all).
_ARGS_JSON = JSONB().with_variant(JSON(), "sqlite")


class ToolRun(Base):
    __tablename__ = "tool_runs"
    __table_args__ = (
        Index("ix_tool_runs_engagement_id_started_at", "engagement_id", "started_at"),
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
    server_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(_ARGS_JSON, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    stderr: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    preset_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'completed'"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
