from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, UniqueConstraint, func, text
from sqlmodel import Field

from app.db.base import Base


class User(Base, table=True):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=254, nullable=False)
    password_hash: str = Field(max_length=255, nullable=False, repr=False, exclude=True)
    is_active: bool = Field(
        default=True, nullable=False, sa_column_kwargs={"server_default": text("true")}
    )
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": func.now()},
    )
