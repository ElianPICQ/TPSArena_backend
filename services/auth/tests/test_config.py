import pytest
from pydantic import ValidationError

from auth.config import SECRETS, Settings


def settings(environment: str, secret: str) -> Settings:
    return Settings(environment=environment, **{name: secret for name in SECRETS})


def test_development_secrets_are_refused_outside_development():
    for weak in ("change-me-dev-" + "x" * 40, "too-short"):
        with pytest.raises(ValidationError, match="real secret"):
            settings("production", weak)
    with pytest.raises(ValidationError):
        Settings(environment="", **{name: "change-me-dev" for name in SECRETS})  # forgot to set it


def test_development_and_real_secrets_are_accepted():
    settings("development", "change-me-dev-secret")
    settings("production", "s" * 32)
