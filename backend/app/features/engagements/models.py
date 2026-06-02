"""SQLAlchemy ORM models for the engagements feature: Engagement, EngagementMember."""

from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db import Base


class Engagement(Base):
    __tablename__ = "engagements"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_engagements_status"),
        CheckConstraint(
            "privacy_mode IN ('local_only', 'cloud_enabled')",
            name="ck_engagements_privacy_mode",
        ),
        Index("ix_engagements_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    client_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    privacy_mode: Mapped[Literal["local_only", "cloud_enabled"]] = mapped_column(
        String(16), nullable=False, server_default=text("'local_only'")
    )
    concurrency_slot_limit: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    members: Mapped[list["EngagementMember"]] = relationship(
        back_populates="engagement",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class EngagementMember(Base):
    __tablename__ = "engagement_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="ck_engagement_members_role"),
        Index("ix_engagement_members_user_id", "user_id"),
    )

    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'member'"))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    engagement: Mapped["Engagement"] = relationship(back_populates="members")
