from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://social_svc:social_dev_password@localhost:5432/tpsarena"
    redis_url: str = "redis://localhost:6379/0"
    auth_url: str = "http://localhost:8001"
    auth_jwks_url: str = "http://localhost:8001/.well-known/jwks.json"
    serverlist_url: str = "http://localhost:8002"
    social_service_secret: str = "change-me-dev-social-service-secret"
    upstream_timeout_seconds: float = 5.0

    # Avatar ids shipped with the game client; a profile can only use one of these.
    avatars: list[str] = ["default", "soldier", "scout", "medic", "engineer", "sniper"]
    bio_max_length: int = 200
    max_friends: int = 200

    max_party_size: int = 8
    invite_ttl_seconds: int = 60

    events_stream_maxlen: int = 100_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
