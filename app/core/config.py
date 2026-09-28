from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    database_url: PostgresDsn
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    redis_url: RedisDsn = "redis://localhost:6379/0"
    redis_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    external_providers_mode: Literal["mock", "live"] = "mock"
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    openai_timeout_seconds: float = Field(default=30, gt=0, le=120)
    openai_max_retries: int = Field(default=2, ge=0, le=3)
    director_image_estimate_usd: Decimal = Field(
        default=Decimal("0.005"), gt=0, le=100, max_digits=12, decimal_places=6
    )
    director_video_second_estimate_usd: Decimal = Field(
        default=Decimal("0.01"), gt=0, le=100, max_digits=12, decimal_places=6
    )
    director_visual_budget_fraction: Decimal = Field(
        default=Decimal("0.5"), gt=0, le=1, decimal_places=4
    )
    jwt_secret_key: SecretStr
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_issuer: str = "ai-slop-backend"
    jwt_audience: str = "ai-slop-api"
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path(__file__).resolve().parents[2] / "data" / "assets"
    storage_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    s3_public_endpoint_url: str | None = None
    s3_url_seconds: int = Field(default=300, ge=1, le=3600)
    s3_timeout_seconds: int = Field(default=10, ge=1, le=60)
    s3_endpoint_url: str = "http://localhost:8333"
    s3_bucket: str = "ai-slop"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_storage(self) -> "Settings":
        if self.storage_backend == "s3" and (
            not self.s3_access_key_id
            or not self.s3_access_key_id.get_secret_value()
            or not self.s3_secret_access_key
            or not self.s3_secret_access_key.get_secret_value()
        ):
            raise ValueError("S3 storage requires access and secret keys")
        return self

    @field_validator("s3_endpoint_url", "s3_public_endpoint_url")
    @classmethod
    def validate_s3_endpoint(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value is None:
            return value
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("S3 endpoint must be an HTTP(S) origin without credentials")
        return value.rstrip("/")

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
