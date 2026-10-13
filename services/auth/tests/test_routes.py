"""Integration tests, against the throwaway Postgres and Redis started in conftest.py."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tpsarena_common.events import CodeEmail, Event, UserDeleted, decode

from auth import security
from auth.config import get_settings
from auth.main import app
from auth.redis_client import redis


@pytest.fixture
async def client():
    # A fresh fake IP per test keeps the per-IP rate limits independent.
    headers = {"x-forwarded-for": f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.1"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


def new_account() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return {"email": f"Player_{suffix}@Example.dev", "username": f"p_{suffix}", "password": "secret123"}


def decoded(fields: dict[str, str]) -> Event | None:
    try:
        return decode(fields)
    except Exception:  # entries written before the shared event format
        return None


async def code_from_stream(email: str, kind: str = "verify") -> str:
    entries = await redis.xrevrange(get_settings().emails_stream, count=200)
    for _, fields in entries:
        event = decoded(fields)
        if isinstance(event, CodeEmail) and event.email == email.lower() and event.kind == kind:
            return event.code
    raise AssertionError(f"no {kind} email queued for {email}")


async def test_register_verify_login_flow(client: httpx.AsyncClient):
    account = new_account()
    login_body = {"email": account["email"], "password": account["password"]}

    r = await client.post("/register", json=account)
    assert r.status_code == 201
    assert "access_token" not in r.json()

    r = await client.post("/login", json=login_body)
    assert r.status_code == 403
    assert r.json()["error"] == "email_not_verified"

    code = await code_from_stream(account["email"])
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    r = await client.post("/verify-email", json={"email": account["email"], "code": wrong})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_code"

    r = await client.post("/verify-email", json={"email": account["email"], "code": code})
    assert r.status_code == 200
    assert {"access_token", "refresh_token", "expires_in"} <= r.json().keys()

    # Single use.
    r = await client.post("/verify-email", json={"email": account["email"], "code": code})
    assert r.status_code == 400

    r = await client.post("/login", json=login_body)
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"

    r = await client.post("/login", json={**login_body, "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_credentials"


async def test_register_duplicates(client: httpx.AsyncClient):
    account = new_account()
    assert (await client.post("/register", json=account)).status_code == 201

    r = await client.post("/register", json={**new_account(), "email": account["email"].upper()})
    assert r.status_code == 409
    assert r.json()["error"] == "email_taken"

    r = await client.post("/register", json={**new_account(), "username": account["username"].upper()})
    assert r.status_code == 409
    assert r.json()["error"] == "username_taken"


async def test_register_validation(client: httpx.AsyncClient):
    r = await client.post("/register", json={**new_account(), "password": "short"})
    assert r.status_code == 422
    r = await client.post("/register", json={**new_account(), "username": "bad name!"})
    assert r.status_code == 422


async def test_unknown_email_login_matches_wrong_password(client: httpx.AsyncClient):
    r = await client.post("/login", json={"email": f"{uuid.uuid4().hex}@example.dev", "password": "secret123"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_credentials"


async def test_code_locked_after_max_attempts(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)
    code = await code_from_stream(account["email"])
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(get_settings().code_max_attempts):
        await client.post("/verify-email", json={"email": account["email"], "code": wrong})
    r = await client.post("/verify-email", json={"email": account["email"], "code": code})
    assert r.status_code == 400


async def test_login_rate_limited(client: httpx.AsyncClient):
    body = {"email": f"{uuid.uuid4().hex}@example.dev", "password": "secret123"}
    statuses = [(await client.post("/login", json=body)).status_code for _ in range(get_settings().login_rate_limit + 1)]
    assert statuses[-1] == 429
    assert set(statuses[:-1]) == {401}


async def verified_tokens(client: httpx.AsyncClient) -> dict:
    account = new_account()
    await client.post("/register", json=account)
    code = await code_from_stream(account["email"])
    r = await client.post("/verify-email", json={"email": account["email"], "code": code})
    assert r.status_code == 200
    return r.json()


async def test_refresh_rotates_token(client: httpx.AsyncClient):
    tokens = await verified_tokens(client)

    r = await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert new["access_token"]

    r = await client.post("/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 200


async def test_reused_refresh_token_revokes_all_sessions(client: httpx.AsyncClient):
    tokens = await verified_tokens(client)
    new = (await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})).json()

    r = await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_refresh_token"

    # The legitimate rotated token is revoked too.
    r = await client.post("/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401


async def test_unknown_refresh_token(client: httpx.AsyncClient):
    r = await client.post("/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient):
    tokens = await verified_tokens(client)

    r = await client.post("/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    assert r.content == b""

    r = await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401

    # Idempotent, and doesn't reveal whether the token existed.
    assert (await client.post("/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204
    assert (await client.post("/logout", json={"refresh_token": "unknown"})).status_code == 204


async def test_resend_replaces_previous_code(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)
    old_code = await code_from_stream(account["email"])

    r = await client.post("/verify-email/resend", json={"email": account["email"]})
    assert r.status_code == 202
    new_code = await code_from_stream(account["email"])

    if new_code != old_code:  # 1 in a million they're equal
        r = await client.post("/verify-email", json={"email": account["email"], "code": old_code})
        assert r.status_code == 400
    r = await client.post("/verify-email", json={"email": account["email"], "code": new_code})
    assert r.status_code == 200


async def test_resend_same_answer_for_unknown_and_verified(client: httpx.AsyncClient):
    tokens_account = new_account()
    await client.post("/register", json=tokens_account)
    code = await code_from_stream(tokens_account["email"])
    await client.post("/verify-email", json={"email": tokens_account["email"], "code": code})
    stream_len = await redis.xlen(get_settings().emails_stream)

    unknown = await client.post("/verify-email/resend", json={"email": f"{uuid.uuid4().hex}@example.dev"})
    verified = await client.post("/verify-email/resend", json={"email": tokens_account["email"]})

    assert unknown.status_code == verified.status_code == 202
    assert unknown.json() == verified.json()
    # No email queued for either.
    assert await redis.xlen(get_settings().emails_stream) == stream_len


async def test_resend_rate_limited_per_email(client: httpx.AsyncClient):
    body = {"email": f"{uuid.uuid4().hex}@example.dev"}
    limit = get_settings().resend_email_rate_limit
    statuses = [(await client.post("/verify-email/resend", json=body)).status_code for _ in range(limit + 1)]
    assert statuses == [202] * limit + [429]


async def test_password_reset_flow(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)
    code = await code_from_stream(account["email"])
    tokens = (await client.post("/verify-email", json={"email": account["email"], "code": code})).json()

    r = await client.post("/password-reset/request", json={"email": account["email"]})
    assert r.status_code == 202
    reset_code = await code_from_stream(account["email"], "reset")

    confirm = {"email": account["email"], "code": reset_code, "new_password": "new-secret-456"}
    wrong = {**confirm, "code": f"{(int(reset_code) + 1) % 10**6:06d}"}
    r = await client.post("/password-reset/confirm", json=wrong)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_code"

    r = await client.post("/password-reset/confirm", json=confirm)
    assert r.status_code == 204

    # Single use.
    assert (await client.post("/password-reset/confirm", json=confirm)).status_code == 400
    # All sessions revoked.
    assert (await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 401
    # Old password rejected, new one works.
    old_login = {"email": account["email"], "password": account["password"]}
    assert (await client.post("/login", json=old_login)).status_code == 401
    r = await client.post("/login", json={**old_login, "password": "new-secret-456"})
    assert r.status_code == 200


async def test_password_reset_verifies_unverified_account(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)

    await client.post("/password-reset/request", json={"email": account["email"]})
    reset_code = await code_from_stream(account["email"], "reset")
    confirm = {"email": account["email"], "code": reset_code, "new_password": "new-secret-456"}
    assert (await client.post("/password-reset/confirm", json=confirm)).status_code == 204

    r = await client.post("/login", json={"email": account["email"], "password": "new-secret-456"})
    assert r.status_code == 200


async def test_verify_code_cannot_reset_password(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)
    verify_code = await code_from_stream(account["email"])
    confirm = {"email": account["email"], "code": verify_code, "new_password": "new-secret-456"}
    assert (await client.post("/password-reset/confirm", json=confirm)).status_code == 400


async def test_password_reset_request_same_answer_for_unknown_email(client: httpx.AsyncClient):
    account = new_account()
    await client.post("/register", json=account)
    stream_len = await redis.xlen(get_settings().emails_stream)

    known = await client.post("/password-reset/request", json={"email": account["email"]})
    unknown = await client.post("/password-reset/request", json={"email": f"{uuid.uuid4().hex}@example.dev"})

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    # Only the real account got an email.
    assert await redis.xlen(get_settings().emails_stream) == stream_len + 1


async def test_password_reset_confirm_unknown_email(client: httpx.AsyncClient):
    confirm = {"email": f"{uuid.uuid4().hex}@example.dev", "code": "123456", "new_password": "new-secret-456"}
    r = await client.post("/password-reset/confirm", json=confirm)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_code"


async def test_password_reset_request_rate_limited(client: httpx.AsyncClient):
    body = {"email": f"{uuid.uuid4().hex}@example.dev"}
    limit = get_settings().resend_email_rate_limit
    statuses = [(await client.post("/password-reset/request", json=body)).status_code for _ in range(limit + 1)]
    assert statuses == [202] * limit + [429]


async def verified_account(client: httpx.AsyncClient) -> tuple[dict[str, str], dict]:
    account = new_account()
    await client.post("/register", json=account)
    code = await code_from_stream(account["email"])
    r = await client.post("/verify-email", json={"email": account["email"], "code": code})
    return account, r.json()


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_get_me(client: httpx.AsyncClient):
    account, tokens = await verified_account(client)
    r = await client.get("/me", headers=bearer(tokens["access_token"]))
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == account["email"].lower()
    assert me["username"] == account["username"]
    assert {"user_id", "created_at"} <= me.keys()


async def test_get_me_without_token(client: httpx.AsyncClient):
    r = await client.get("/me")
    assert r.status_code == 401
    assert r.json()["error"] == "not_authenticated"


async def test_get_me_with_garbage_token(client: httpx.AsyncClient):
    r = await client.get("/me", headers=bearer("not.a.jwt"))
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"


async def test_get_me_with_expired_token(client: httpx.AsyncClient):
    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": past, "exp": past + timedelta(minutes=15)},
        security._private_key(),
        algorithm="EdDSA",
    )
    r = await client.get("/me", headers=bearer(token))
    assert r.status_code == 401
    assert r.json()["error"] == "token_expired"


async def test_get_me_with_token_signed_by_another_key(client: httpx.AsyncClient):
    _, tokens = await verified_account(client)
    sub = jwt.decode(tokens["access_token"], options={"verify_signature": False})["sub"]
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": sub, "iat": now, "exp": now + timedelta(minutes=15)},
        Ed25519PrivateKey.generate(),
        algorithm="EdDSA",
    )
    r = await client.get("/me", headers=bearer(forged))
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"


async def test_delete_me(client: httpx.AsyncClient):
    account, tokens = await verified_account(client)
    user_id = (await client.get("/me", headers=bearer(tokens["access_token"]))).json()["user_id"]
    headers = bearer(tokens["access_token"])

    r = await client.post("/me/delete", json={"password": "wrong-password"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"] == "wrong_password"

    r = await client.post("/me/delete", json={"password": account["password"]}, headers=headers)
    assert r.status_code == 204

    # Still-valid token, but the account is gone.
    r = await client.get("/me", headers=headers)
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"
    assert (await client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 401
    login = {"email": account["email"], "password": account["password"]}
    assert (await client.post("/login", json=login)).status_code == 401

    entries = await redis.xrevrange(get_settings().user_deleted_stream, count=50)
    assert any(isinstance(e := decoded(fields), UserDeleted) and str(e.user_id) == user_id for _, fields in entries)

    # The email is free again.
    assert (await client.post("/register", json=account)).status_code == 201


async def test_delete_me_requires_token(client: httpx.AsyncClient):
    r = await client.post("/me/delete", json={"password": "secret123"})
    assert r.status_code == 401


async def test_delete_me_password_attempts_rate_limited(client: httpx.AsyncClient):
    _, tokens = await verified_account(client)
    headers = bearer(tokens["access_token"])
    limit = get_settings().delete_account_rate_limit
    statuses = [
        (await client.post("/me/delete", json={"password": "wrong-password"}, headers=headers)).status_code
        for _ in range(limit + 1)
    ]
    assert statuses == [403] * limit + [429]


async def test_get_users(client: httpx.AsyncClient):
    first, tokens = await verified_account(client)
    second, second_tokens = await verified_account(client)
    first_id = (await client.get("/me", headers=bearer(tokens["access_token"]))).json()["user_id"]
    second_id = (await client.get("/me", headers=bearer(second_tokens["access_token"]))).json()["user_id"]
    unknown_id = str(uuid.uuid4())

    ids = f"{first_id}, {second_id},{unknown_id},{first_id}"
    r = await client.get("/users", params={"ids": ids}, headers=bearer(tokens["access_token"]))
    assert r.status_code == 200
    users = {u["user_id"]: u for u in r.json()["users"]}
    # Unknown ids left out, duplicates merged, never the email.
    assert users == {
        first_id: {"user_id": first_id, "username": first["username"]},
        second_id: {"user_id": second_id, "username": second["username"]},
    }


async def test_get_users_requires_token(client: httpx.AsyncClient):
    r = await client.get("/users", params={"ids": str(uuid.uuid4())})
    assert r.status_code == 401


async def test_get_users_invalid_ids(client: httpx.AsyncClient):
    _, tokens = await verified_account(client)
    headers = bearer(tokens["access_token"])

    r = await client.get("/users", params={"ids": "not-a-uuid"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_ids"

    too_many = ",".join(str(uuid.uuid4()) for _ in range(get_settings().users_lookup_max_ids + 1))
    r = await client.get("/users", params={"ids": too_many}, headers=headers)
    assert r.status_code == 400
    assert r.json()["error"] == "too_many_ids"

    r = await client.get("/users", params={"ids": ""}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"users": []}


async def test_jwks_verifies_issued_tokens(client: httpx.AsyncClient):
    r = await client.get("/.well-known/jwks.json")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    keys = r.json()["keys"]
    assert len(keys) == 1
    jwk = keys[0]
    assert jwk["kid"] == get_settings().jwt_key_id
    assert (jwk["kty"], jwk["crv"], jwk["alg"], jwk["use"]) == ("OKP", "Ed25519", "EdDSA", "sig")
    assert "d" not in jwk  # the private part must never be published

    # Check a token the way the other services will: pick the key by the token's kid.
    _, tokens = await verified_account(client)
    token = tokens["access_token"]
    kid = jwt.get_unverified_header(token)["kid"]
    key = next(k for k in keys if k["kid"] == kid)
    claims = jwt.decode(token, jwt.PyJWK(key).key, algorithms=["EdDSA"])
    assert uuid.UUID(claims["sub"])
