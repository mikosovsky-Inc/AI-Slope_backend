from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: PostgresDsn
    jwt_secret_key: SecretStr
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_issuer: str = "ai-slop-backend"
    jwt_audience: str = "ai-slop-api"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_bucket: str = "ai-slop"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def require_psycopg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+psycopg":
            raise ValueError("Use a postgresql+psycopg:// database URL")
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT secret must contain at least 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
