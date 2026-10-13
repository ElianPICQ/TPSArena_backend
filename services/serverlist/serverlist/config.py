from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SECRETS = ("game_server_secret", "social_service_secret")
MIN_SECRET_LENGTH = 32


def is_weak(secret: str) -> bool:
    return secret.startswith("change-me") or len(secret) < MIN_SECRET_LENGTH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    auth_jwks_url: str = "http://localhost:8001/.well-known/jwks.json"

    # Game servers register with this; every other call uses the token returned by registration.
    game_server_secret: str = "change-me-dev-game-server-secret"
    # Social authenticates with this to allocate servers for parties.
    social_service_secret: str = "change-me-dev-social-service-secret"

    heartbeat_interval_seconds: int = 5
    server_ttl_seconds: int = 15  # a server missing heartbeats this long is gone
    sweep_interval_seconds: float = 5  # how often gone servers are removed (and server.removed published)
    ticket_ttl_seconds: int = 30
    # How long a reserved server waits for the party before it may go back to idle.
    reservation_seconds: int = 35
    max_players: int = 8

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
