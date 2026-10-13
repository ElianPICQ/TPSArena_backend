from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_url: str = "http://localhost:8001"
    serverlist_url: str = "http://localhost:8002"
    social_url: str = "http://localhost:8003"
    stats_url: str = "http://localhost:8004"
    upstream_timeout_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
