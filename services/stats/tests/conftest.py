"""Tests need Postgres (stats schema migrated) and Redis from docker compose. Redis database 12.

The dev database is shared, so leaderboards contain other rows: tests give their players huge
totals and check ranks relative to each other.
"""

import os

os.environ["REDIS_URL"] = "redis://localhost:6379/12"

import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402
from tpsarena_common.events import Event, MatchFinished, MatchPlayerResult, decode  # noqa: E402

from stats.deps import verifier  # noqa: E402
from stats.main import app  # noqa: E402
from stats.redis_client import redis  # noqa: E402
from stats.worker import handle  # noqa: E402

KEY = Ed25519PrivateKey.generate()


def token_headers(user_id: uuid.UUID | None = None) -> dict[str, str]:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id or uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=15)}
    return {"authorization": f"Bearer {jwt.encode(claims, KEY, algorithm='EdDSA', headers={'kid': 'test'})}"}


@pytest.fixture(scope="session", autouse=True)
def trust_test_key():
    jwk = OKPAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    verifier.load_jwks({"keys": [{**jwk, "kid": "test", "alg": "EdDSA", "use": "sig"}]})


@pytest.fixture(autouse=True)
async def clean_redis():
    await redis.flushdb()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=token_headers()) as c:
        yield c


def result(user_id: uuid.UUID, kills: int = 0, deaths: int = 0, placement: int = 2) -> MatchPlayerResult:
    return MatchPlayerResult(user_id=user_id, kills=kills, deaths=deaths, placement=placement)


async def play(
    *results: MatchPlayerResult, match_id: uuid.UUID | None = None, ended_at: datetime | None = None
) -> MatchFinished:
    """A game server reports a match; the worker handles it."""
    end = ended_at or datetime.now(UTC)
    event = MatchFinished(
        match_id=match_id or uuid.uuid4(),
        server_id=uuid.uuid4(),
        map="arena",
        started_at=end - timedelta(minutes=10),
        ended_at=end,
        players=list(results),
    )
    await handle(event)
    return event


async def progress_events() -> list[Event]:
    return [decode(fields) for _, fields in await redis.xrange("stats.progress")]
