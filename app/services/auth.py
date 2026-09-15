from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.security import DUMMY_PASSWORD_HASH, password_hasher
from app.models.user import User
from app.schemas.user import Credentials, UserCreate


class EmailAlreadyRegistered(Exception):
    pass


def register_user(db: Session, data: UserCreate) -> User:
    user = User(
        email=str(data.email), password_hash=password_hasher.hash(data.password.get_secret_value())
    )
    db.add(user)
    try:
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
