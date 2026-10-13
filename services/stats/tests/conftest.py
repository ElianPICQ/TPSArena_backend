"""Throwaway Postgres and Redis containers for this test run (needs Docker; nothing to start by hand).

Started at import, before the app is imported: settings and clients are created at import time.
"""

import atexit
import os
import subprocess
import sys
from pathlib import Path

from testcontainers.community.redis import RedisContainer
from testcontainers.community.postgres import PostgresContainer

SERVICE_DIR = Path(__file__).resolve().parent.parent

postgres = PostgresContainer("postgres:17", username="stats_svc", password="test", dbname="stats", driver="asyncpg")
redis_container = RedisContainer("redis:7")
postgres.start()
redis_container.start()
atexit.register(postgres.stop)
atexit.register(redis_container.stop)

os.environ["DATABASE_URL"] = postgres.get_connection_url()
os.environ["REDIS_URL"] = (
    f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
)

subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE_DIR, check=True, env=os.environ)

import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402
from tpsarena_common.events import Event, MatchFinished, MatchPlayerResult, decode  # noqa: E402

from sqlalchemy import text  # noqa: E402

from stats.db import SessionLocal  # noqa: E402
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
async def clean_state():
    """Every test starts with empty tables and an empty Redis (this run's own containers)."""
    async with SessionLocal() as session:
        await session.execute(text("TRUNCATE stats.matches, stats.match_players, stats.player_stats CASCADE"))
        await session.commit()
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
