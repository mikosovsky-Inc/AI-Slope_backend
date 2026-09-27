from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Index, Numeric, UniqueConstraint, func
from sqlmodel import Field

from app.db.base import Base


class ChannelStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class AutopilotMode(StrEnum):
    MANUAL = "manual"
    SEMI_AUTO = "semi_auto"


class Channel(Base, table=True):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint("language IN ('pl', 'en')", name="ck_channels_language"),
        CheckConstraint("videos_per_day BETWEEN 1 AND 24", name="ck_channels_frequency"),
        CheckConstraint(
            "budget_per_video_usd > 0 AND budget_per_video_usd <= 100", name="ck_channels_budget"
        ),
        Index("ix_channels_owner_created", "owner_id", "created_at", "id"),
        Index("ix_channels_status", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE", nullable=False)
    name: str = Field(max_length=120)
    idea: str = Field(max_length=4000)
    language: str = Field(max_length=2)
    videos_per_day: int = Field(default=2)
    budget_per_video_usd: Decimal = Field(sa_type=Numeric(10, 4))
    status: ChannelStatus = Field(
        default=ChannelStatus.DRAFT,
        sa_type=Enum(
            ChannelStatus,
            values_callable=lambda values: [v.value for v in values],
            native_enum=False,
            create_constraint=True,
            name="channel_status",
        ),
    )
    autopilot_mode: AutopilotMode = Field(
        default=AutopilotMode.MANUAL,
        sa_type=Enum(
            AutopilotMode,
            values_callable=lambda values: [v.value for v in values],
            native_enum=False,
            create_constraint=True,
            name="autopilot_mode",
        ),
    )
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": func.now()},
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True), nullable=False
    )


class ChannelBlueprint(Base, table=True):
    __tablename__ = "channel_blueprints"
    __table_args__ = (UniqueConstraint("channel_id", name="uq_blueprints_channel"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channels.id", ondelete="CASCADE", nullable=False)
    configuration: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True), nullable=False
    )


class ContentPillar(Base, table=True):
    __tablename__ = "content_pillars"
    __table_args__ = (
        UniqueConstraint("blueprint_id", "position", name="uq_pillars_position"),
        CheckConstraint("position >= 0", name="ck_pillars_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    blueprint_id: UUID = Field(
        foreign_key="channel_blueprints.id", ondelete="CASCADE", nullable=False
    )
    name: str = Field(max_length=120)
    description: str = Field(max_length=2000)
    position: int
