from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://stats_svc:stats_dev_password@localhost:5432/tpsarena"
    redis_url: str = "redis://localhost:6379/0"
    auth_jwks_url: str = "http://localhost:8001/.well-known/jwks.json"

    # XP per match: for playing, per kill, for 1st place.
    xp_per_match: int = 100
    xp_per_kill: int = 10
    xp_for_win: int = 200
    # Going from level L to L+1 takes level_base_xp + level_step_xp * (L - 1).
    level_base_xp: int = 1000
    level_step_xp: int = 250

    match_history_page_max: int = 50
    leaderboard_page_max: int = 100
    leaderboard_radius_max: int = 25

    events_stream_maxlen: int = 100_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
