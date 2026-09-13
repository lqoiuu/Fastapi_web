from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TICKETING_",
        extra="ignore",
    )

    app_name: str = "FastAPI Ticketing System"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_v1_prefix: str = Field(default="/api/v1", pattern=r"^/[^/].*[^/]$|^/.$")
    database_url: str = "postgresql+asyncpg://ticketing:ticketing@127.0.0.1:5432/ticketing"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    auth_secret_key: SecretStr = SecretStr("development-only-change-me-32-chars")
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=30)
    attachment_storage_path: Path = Path("var/attachments")
    attachment_max_size_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_timeout_seconds: float = Field(default=0.5, gt=0, le=5)
    permission_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    permission_cache_ttl_jitter_seconds: int = Field(default=30, ge=0, le=300)
    login_rate_limit_requests: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    background_job_max_attempts: int = Field(default=5, ge=1, le=20)
    background_job_lease_seconds: int = Field(default=120, ge=10, le=3600)
    background_job_soft_time_limit_seconds: int = Field(default=60, ge=1, le=3600)
    background_job_hard_time_limit_seconds: int = Field(default=75, ge=2, le=3600)
    background_job_retry_base_seconds: int = Field(default=2, ge=1, le=300)
    background_job_retry_max_seconds: int = Field(default=300, ge=1, le=3600)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_poll_seconds: float = Field(default=2.0, ge=0.5, le=60)
    export_storage_path: Path = Path("var/exports")
    export_max_rows: int = Field(default=10_000, ge=1, le=100_000)
    email_backend: Literal["console", "smtp"] = "console"
    email_from: EmailStr = "support@example.com"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True

    @model_validator(mode="after")
    def reject_insecure_production_secret(self) -> Self:
        secret = self.auth_secret_key.get_secret_value()
        insecure_values = {
            "development-only-change-me-32-chars",
            "replace-with-at-least-32-random-characters",
        }
        if self.environment == "production" and (len(secret) < 32 or secret in insecure_values):
            raise ValueError("Production requires a non-placeholder auth secret of 32+ characters")
        if (
            self.background_job_hard_time_limit_seconds
            <= self.background_job_soft_time_limit_seconds
        ):
            raise ValueError("Background job hard time limit must exceed the soft time limit")
        if self.background_job_retry_max_seconds < self.background_job_retry_base_seconds:
            raise ValueError("Background job retry maximum must be at least the retry base")
        if self.email_backend == "smtp" and self.smtp_host is None:
            raise ValueError("SMTP email backend requires smtp_host")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""
    return Settings()
