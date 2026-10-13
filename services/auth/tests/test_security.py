import uuid
from pathlib import Path

import jwt

from auth import security
from auth.config import get_settings


def test_password_hash_roundtrip():
    h = security.hash_password("secret123")
    assert h.startswith("$argon2")
    assert security.verify_password("secret123", h)
    assert not security.verify_password("wrong", h)


def test_code_is_six_digits_and_hmac_matches():
    code = security.generate_code()
    assert len(code) == 6 and code.isdigit()
    h = security.hash_code(code)
    assert security.code_matches(code, h)
    assert not security.code_matches(f"{(int(code) + 1) % 10**6:06d}", h)


def test_access_token_claims():
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id)
    public_key = (Path(get_settings().jwt_private_key_path).parent / "jwt_public.pem").read_bytes()
    claims = jwt.decode(token, public_key, algorithms=["EdDSA"])
    assert claims["sub"] == str(user_id)
    assert claims["exp"] - claims["iat"] == get_settings().access_token_ttl_seconds
    assert jwt.get_unverified_header(token)["kid"] == get_settings().jwt_key_id


def test_refresh_token_hash_is_stable():
    token = security.generate_refresh_token()
    assert security.hash_refresh_token(token) == security.hash_refresh_token(token)
    assert security.hash_refresh_token(token) != token
