from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AccessTokenClaims(BaseModel):
    sub: UUID
    exp: int
    iat: int
    iss: str
    aud: str
    token_type: Literal["access"]
