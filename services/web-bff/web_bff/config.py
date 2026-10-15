from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_url: str = "http://localhost:8001"
    social_url: str = "http://localhost:8003"
    stats_url: str = "http://localhost:8004"
    realtime_url: str = "http://localhost:8005"
    upstream_timeout_seconds: float = 5.0

    # The refresh token lives in an HttpOnly cookie, so page scripts never see it.
    # The path is the one the browser sees: the site's nginx serves this service under /api/.
    refresh_cookie_name: str = "tpsarena_refresh"
    refresh_cookie_path: str = "/api/auth"
    refresh_cookie_secure: bool = True  # browsers also accept Secure cookies on http://localhost
    refresh_cookie_max_age_days: int = 30  # Auth's refresh_token_ttl_days


@lru_cache
def get_settings() -> Settings:
    return Settings()
