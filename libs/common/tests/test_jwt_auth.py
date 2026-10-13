import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from tpsarena_common.jwt_auth import JWKSVerifier, TokenError

JWKS_URL = "http://auth.test/.well-known/jwks.json"


def jwks_for(key: Ed25519PrivateKey, kid: str) -> dict:
    jwk = OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "alg": "EdDSA", "use": "sig"}]}


def token(key: Ed25519PrivateKey, kid: str, sub: str, expires_in: timedelta = timedelta(minutes=15)) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": sub, "iat": now, "exp": now + expires_in}, key, algorithm="EdDSA", headers={"kid": kid})


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@respx.mock
async def test_fetches_jwks_once_and_decodes(key):
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_for(key, "k1")))
    verifier = JWKSVerifier(JWKS_URL)
    user_id = uuid.uuid4()

    assert await verifier.decode(token(key, "k1", str(user_id))) == user_id
    assert await verifier.decode(token(key, "k1", str(user_id))) == user_id
    assert route.call_count == 1  # cached


@respx.mock
async def test_unknown_kid_refetches_for_rotation(key):
    new_key = Ed25519PrivateKey.generate()
    route = respx.get(JWKS_URL).mock(
        side_effect=[httpx.Response(200, json=jwks_for(key, "k1")), httpx.Response(200, json=jwks_for(new_key, "k2"))]
    )
    verifier = JWKSVerifier(JWKS_URL, refetch_cooldown_seconds=0)
    await verifier.decode(token(key, "k1", str(uuid.uuid4())))
    user_id = uuid.uuid4()
    assert await verifier.decode(token(new_key, "k2", str(user_id))) == user_id
    assert route.call_count == 2


@respx.mock
async def test_unknown_kid_refetch_has_cooldown(key):
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_for(key, "k1")))
    verifier = JWKSVerifier(JWKS_URL, refetch_cooldown_seconds=60)
    for _ in range(5):
        with pytest.raises(TokenError) as exc:
            await verifier.decode(token(key, "made-up", str(uuid.uuid4())))
        assert exc.value.error == "invalid_token"
    assert route.call_count == 1


async def test_rejects_token_signed_by_another_key(key):
    verifier = JWKSVerifier(JWKS_URL)
    verifier.load_jwks(jwks_for(key, "k1"))
    forged = token(Ed25519PrivateKey.generate(), "k1", str(uuid.uuid4()))
    with pytest.raises(TokenError) as exc:
        await verifier.decode(forged)
    assert exc.value.error == "invalid_token"


async def test_expired_token(key):
    verifier = JWKSVerifier(JWKS_URL)
    verifier.load_jwks(jwks_for(key, "k1"))
    with pytest.raises(TokenError) as exc:
        await verifier.decode(token(key, "k1", str(uuid.uuid4()), expires_in=timedelta(minutes=-1)))
    assert exc.value.error == "token_expired"


async def test_rejects_other_algorithms(key):
    verifier = JWKSVerifier(JWKS_URL)
    verifier.load_jwks(jwks_for(key, "k1"))
    claims = {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)}
    hs = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(TokenError):
        await verifier.decode(hs)


@respx.mock
async def test_auth_down_gives_invalid_token(key):
    respx.get(JWKS_URL).mock(side_effect=httpx.ConnectError("refused"))
    verifier = JWKSVerifier(JWKS_URL)
    with pytest.raises(TokenError) as exc:
        await verifier.decode(token(key, "k1", str(uuid.uuid4())))
    assert exc.value.error == "invalid_token"
