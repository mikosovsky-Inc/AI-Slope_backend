from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.schemas.auth import AccessTokenClaims, TokenResponse

password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("dummy-password-for-timing-equalization")


def create_access_token(user_id: UUID, settings: Settings) -> TokenResponse:
    now = datetime.now(UTC)
    lifetime = timedelta(minutes=settings.jwt_access_token_minutes)
    claims = AccessTokenClaims(
        sub=user_id,
        iat=int(now.timestamp()),
        exp=int((now + lifetime).timestamp()),
        iss=settings.jwt_issuer,
        aud=settings.jwt_audience,
        token_type="access",
    )
    token = jwt.encode(
        claims.model_dump(mode="json"),
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    return TokenResponse(access_token=token, expires_in=int(lifetime.total_seconds()))


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    payload = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "exp", "iat", "iss", "aud", "token_type"]},
    )
    return AccessTokenClaims.model_validate(payload)
