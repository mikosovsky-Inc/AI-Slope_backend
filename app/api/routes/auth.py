from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import AppSettings, CurrentUser, DbSession, unauthorized
from app.core.security import create_access_token
from app.schemas.auth import TokenResponse
from app.schemas.user import Credentials, UserCreate, UserRead
from app.services.auth import EmailAlreadyRegistered, authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(data: UserCreate, db: DbSession) -> UserRead:
    try:
        user = register_user(db, data)
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="Email already registered") from None
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    data: Credentials, response: Response, db: DbSession, settings: AppSettings
) -> TokenResponse:
    user = authenticate_user(db, data)
    if user is None:
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return create_access_token(user.id, settings)


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
