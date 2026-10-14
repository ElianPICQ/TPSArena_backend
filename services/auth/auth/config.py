from functools import lru_cache
from typing import Self
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SECRETS = ("code_secret",)
MIN_SECRET_LENGTH = 32


def is_weak(secret: str) -> bool:
    return secret.startswith("change-me") or len(secret) < MIN_SECRET_LENGTH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://auth_svc:auth_dev_password@localhost:5432/tpsarena"
    redis_url: str = "redis://localhost:6379/0"

    jwt_private_key_path: Path = Path("keys/jwt_private.pem")  # relative to services/auth
    jwt_key_id: str = "auth-1"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_days: int = 30

    code_secret: str = "change-me-dev-code-secret"
    code_ttl_minutes: int = 15
    code_max_attempts: int = 5

    login_rate_limit: int = 10  # per minute, per IP and per email
    verify_rate_limit: int = 10  # per 15 minutes, per IP and per email
    resend_email_rate_limit: int = 3  # code emails per 15 minutes, per email
    resend_ip_rate_limit: int = 10  # code emails per 15 minutes, per IP
    delete_account_rate_limit: int = 5  # password attempts per 15 minutes, per user
    users_lookup_max_ids: int = 100

    unverified_account_ttl_days: int = 7
    cleanup_interval_seconds: int = 3600
    username_lookup_rate_limit: int = 30  # per minute, per user
    username_search_max_results: int = 20

    emails_stream: str = "auth.emails"
    emails_stream_maxlen: int = 10_000
    user_deleted_stream: str = "user.deleted"
    user_deleted_stream_maxlen: int = 100_000
    sessions_stream_maxlen: int = 100_000

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@tpsarena.local"
    smtp_starttls: bool = False

    # "development" accepts the change-me-dev-* defaults above. Anything else (the default, so a
    # deployment that forgets to set it is covered) refuses to start with a weak secret.
    environment: str = "production"

    @model_validator(mode="after")
    def no_development_secrets(self) -> Self:
        if self.environment != "development":
            weak = [name.upper() for name in SECRETS if is_weak(getattr(self, name))]
            if weak:
                raise ValueError(
                    f"{', '.join(weak)}: set a real secret of at least {MIN_SECRET_LENGTH} characters "
                    "(the change-me-dev-* defaults are only for ENVIRONMENT=development)"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
