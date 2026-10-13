"""Tests need Postgres (social schema migrated) and Redis from docker compose. Redis database 14."""

import os

os.environ["REDIS_URL"] = "redis://localhost:6379/14"
os.environ["AUTH_URL"] = "http://auth.test"
os.environ["SERVERLIST_URL"] = "http://serverlist.test"

import uuid  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402
from tpsarena_common.events import Event, decode  # noqa: E402

from social.deps import verifier  # noqa: E402
from social.main import app  # noqa: E402
from social.redis_client import redis  # noqa: E402

KEY = Ed25519PrivateKey.generate()
DELETED_USERS: set[str] = set()  # ids Auth answers as unknown


@dataclass
class Player:
    user_id: uuid.UUID
    headers: dict[str, str]


def new_player() -> Player:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=15)}
    token = jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "test"})
    return Player(user_id, {"authorization": f"Bearer {token}"})


@pytest.fixture(scope="session", autouse=True)
def trust_test_key():
    jwk = OKPAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    verifier.load_jwks({"keys": [{**jwk, "kid": "test", "alg": "EdDSA", "use": "sig"}]})


@pytest.fixture(autouse=True)
async def clean_redis():
    await redis.flushdb()


def auth_users(request: httpx.Request) -> httpx.Response:
    ids = [i for i in request.url.params["ids"].split(",") if i not in DELETED_USERS]
    return httpx.Response(200, json={"users": [{"user_id": i, "username": f"u{i[:4]}"} for i in ids]})


@pytest.fixture
def mocked():
    """Auth and Server List stand-ins. Tests set `mocked.allocation` to change the Server List answer."""
    with respx.mock(assert_all_called=False) as router:
        router.get("http://auth.test/users").mock(side_effect=auth_users)
        yield router


@pytest.fixture
async def client(mocked):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def events(type_: str | None = None) -> list[Event]:
    """Events published on social.events, oldest first."""
    entries = await redis.xrange("social.events")
    result = [decode(fields) for _, fields in entries]
    return [e for e in result if type_ is None or e.TYPE == type_]
