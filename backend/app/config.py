"""Validated application settings loaded from environment variables."""

from __future__ import annotations

import socket
import sys
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FB_", env_file=None, extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db"
    )
    redis_url: str = Field(description="Redis URL, e.g. redis://redis:6379/0")
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        description="Comma-separated browser origins allowed for unsafe requests and WebSockets"
    )
    instance_id: str = Field(default_factory=socket.gethostname)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    cookie_secure: bool = True
    session_ttl_minutes: int = Field(default=480, ge=5, le=7 * 24 * 60)
    allow_registration: bool = True

    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_failure_window_minutes: int = Field(default=15, ge=1, le=24 * 60)
    ip_rate_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    max_body_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    chat_messages_per_minute: int = Field(default=30, ge=1, le=1000)

    resync_interval_seconds: int = Field(default=15, ge=2, le=300)
    ws_revalidate_seconds: int = Field(default=30, ge=1, le=600)
    ws_queue_size: int = Field(default=100, ge=1, le=10_000)
    ws_max_connections_per_user: int = Field(default=10, ge=1, le=100)
    redis_publish_timeout_seconds: float = Field(default=0.5, gt=0, le=10)

    db_pool_size: int = Field(default=10, ge=1, le=100)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _check_db_url(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("must start with postgresql+psycopg:// (PostgreSQL is required)")
        return value

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("must start with redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def _check_production(self) -> Settings:
        if not self.allowed_origins:
            raise ValueError("FB_ALLOWED_ORIGINS must list at least one origin")
        for origin in self.allowed_origins:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError(f"invalid origin {origin!r}; wildcards are not allowed")
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("FB_COOKIE_SECURE must be true in production")
            if any(o.startswith("http://") for o in self.allowed_origins):
                raise ValueError("production origins must use https://")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings_or_exit() -> Settings:
    """Load settings, or print one readable line per problem and exit with status 2."""
    try:
        return get_settings()
    except ValidationError as exc:
        lines = []
        for e in exc.errors():
            field = "_".join(str(p) for p in e["loc"]).upper()
            lines.append(f"  FB_{field}: {e['msg']}" if field else f"  {e['msg']}")
        sys.stderr.write("Invalid configuration:\n" + "\n".join(lines) + "\n")
        raise SystemExit(2) from None
