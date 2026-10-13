"""A throwaway Redis container for this test run (needs Docker; nothing to start by hand).

Started at import, before the app is imported: settings and clients are created at import time.
"""

import atexit
import os

from testcontainers.community.redis import RedisContainer

redis_container = RedisContainer("redis:7")
redis_container.start()
atexit.register(redis_container.stop)

os.environ["REDIS_URL"] = (
    f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
)
os.environ["ENVIRONMENT"] = "development"  # the dev secret defaults are fine here
os.environ["GAME_SERVER_SECRET"] = "test-game-server-secret"
os.environ["SOCIAL_SERVICE_SECRET"] = "test-social-secret"

import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402

from serverlist.deps import verifier  # noqa: E402
from serverlist.main import app  # noqa: E402
from serverlist.redis_client import redis  # noqa: E402

PLAYER_KEY = Ed25519PrivateKey.generate()
GAME_SERVER = {"authorization": "Bearer test-game-server-secret"}
SOCIAL = {"authorization": "Bearer test-social-secret"}


@pytest.fixture(scope="session", autouse=True)
async def setup():
    # Stand-in for Auth's JWKS: the verifier trusts this test key.
    jwk = OKPAlgorithm.to_jwk(PLAYER_KEY.public_key(), as_dict=True)
    verifier.load_jwks({"keys": [{**jwk, "kid": "test", "alg": "EdDSA", "use": "sig"}]})
    await redis.flushdb()
    yield


@pytest.fixture(autouse=True)
async def clean_redis():
    await redis.flushdb()


def player(user_id: uuid.UUID | None = None) -> dict[str, str]:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id or uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=15)}
    token = jwt.encode(claims, PLAYER_KEY, algorithm="EdDSA", headers={"kid": "test"})
    return {"authorization": f"Bearer {token}"}


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
