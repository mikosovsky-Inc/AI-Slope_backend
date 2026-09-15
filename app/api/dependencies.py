from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: DbSession,
    settings: AppSettings,
) -> User:
    if credentials is None:
        raise unauthorized()
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except (InvalidTokenError, ValidationError):
        raise unauthorized() from None
    user = db.get(User, claims.sub)
    if user is None or not user.is_active:
        raise unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
