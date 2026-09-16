from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.security import DUMMY_PASSWORD_HASH, password_hasher
from app.models.roles import UserRole
from app.models.user import User
from app.schemas.user import Credentials, UserCreate


class EmailAlreadyRegistered(Exception):
    pass


def register_user(db: Session, data: UserCreate) -> User:
    # Hash before taking the lock to keep the registration transaction short.
    password_hash = password_hasher.hash(data.password.get_secret_value())
    try:
        if db.get_bind().dialect.name == "postgresql":
            # Serialize the empty-table check and insert across API processes.
            # The lock is held until commit/rollback; ordinary reads remain possible.
            db.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
        first_user = db.exec(select(User.id).limit(1)).first() is None
        user = User(
            email=str(data.email),
            password_hash=password_hash,
            role=UserRole.ADMIN if first_user else UserRole.USER,
        )
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_users_email":
            raise EmailAlreadyRegistered from exc
        raise
    db.refresh(user)
    return user


def authenticate_user(db: Session, data: Credentials) -> User | None:
    user = db.exec(select(User).where(User.email == str(data.email))).one_or_none()
    stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    valid = password_hasher.verify(data.password.get_secret_value(), stored_hash)
    if not valid or user is None or not user.is_active:
        return None
    return user


def has_users(db: Session) -> bool:
    return db.exec(select(User.id).limit(1)).first() is not None
