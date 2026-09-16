from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, UniqueConstraint, func, text
from sqlmodel import Field

from app.db.base import Base
from app.models.roles import UserRole


class User(Base, table=True):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=254, nullable=False)
    password_hash: str = Field(max_length=255, nullable=False, repr=False, exclude=True)
    role: UserRole = Field(
        default=UserRole.USER,
        sa_type=Enum(
            UserRole,
            values_callable=lambda roles: [role.value for role in roles],
            native_enum=False,
            create_constraint=True,
            name="user_role",
            length=5,
        ),
        nullable=False,
        sa_column_kwargs={"server_default": "user"},
    )
    is_active: bool = Field(
        default=True, nullable=False, sa_column_kwargs={"server_default": text("true")}
    )
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": func.now()},
    )
