from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SECRETS = ("social_service_secret",)
MIN_SECRET_LENGTH = 32


def is_weak(secret: str) -> bool:
    return secret.startswith("change-me") or len(secret) < MIN_SECRET_LENGTH


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

    # Saved classes (game equipment). Weapon ids are the game's WeaponData.id values.
    max_loadouts: int = 10
    loadout_name_max_length: int = 24
    primary_weapons: list[str] = ["smg", "shotgun", "sniper", "rocket", "flamer"]
    secondary_weapons: list[str] = ["laser", "katana", "poop"]

    max_party_size: int = 16
    invite_ttl_seconds: int = 60

    events_stream_maxlen: int = 100_000

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
