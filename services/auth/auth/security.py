import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from jwt.algorithms import OKPAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from auth.config import get_settings

password_hasher = PasswordHash((Argon2Hasher(),))

# Verified against when the email is unknown, so login takes about the same time either way.
DUMMY_PASSWORD_HASH = password_hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


@lru_cache
def _private_key() -> bytes:
    return get_settings().jwt_private_key_path.read_bytes()


@lru_cache
def _public_key() -> Ed25519PublicKey:
    return serialization.load_pem_private_key(_private_key(), password=None).public_key()


def jwks() -> dict:
    """Public keys other services use to check access tokens, served at /.well-known/jwks.json.

    Only the current key for now. To rotate keys, also publish the previous key here until the
    last tokens it signed have expired (15 minutes).
    """
    jwk = OKPAlgorithm.to_jwk(_public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": get_settings().jwt_key_id, "alg": "EdDSA", "use": "sig"}]}


def decode_access_token(token: str) -> uuid.UUID:
    """Return the user id in a valid access token. Raises jwt.InvalidTokenError otherwise."""
    claims = jwt.decode(
        token,
        _public_key(),
        algorithms=["EdDSA"],  # never let the token pick its own algorithm
        options={"require": ["exp", "sub"]},
    )
    return uuid.UUID(claims["sub"])


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, _private_key(), algorithm="EdDSA", headers={"kid": settings.jwt_key_id})


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(code: str) -> str:
    # HMAC with a server secret: a plain hash of 6 digits could be reversed by trying all 10^6 values.
    return hmac.new(get_settings().code_secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def code_matches(code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code(code), code_hash)
