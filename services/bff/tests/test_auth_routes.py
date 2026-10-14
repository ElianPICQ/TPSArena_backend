import httpx
import pytest
import respx

from bff.config import get_settings

AUTH = get_settings().auth_url


AUTH_PATHS = [
    "/register",
    "/verify-email",
    "/verify-email/resend",
    "/login",
    "/refresh",
    "/logout",
    "/password-reset/request",
    "/password-reset/confirm",
]


@pytest.mark.parametrize("path", AUTH_PATHS)
@respx.mock
async def test_forwards_body_status_and_client_ip(client: httpx.AsyncClient, path: str):
    route = respx.post(f"{AUTH}{path}").mock(
        return_value=httpx.Response(403, json={"error": "email_not_verified", "message": "..."})
    )
    r = await client.post(f"/auth{path}", json={"email": "a@b.dev", "password": "secret123"})

    assert r.status_code == 403
    assert r.json() == {"error": "email_not_verified", "message": "..."}
    sent = route.calls.last.request
    assert sent.headers["x-forwarded-for"] == "203.0.113.7"
    assert sent.content == b'{"email":"a@b.dev","password":"secret123"}'


@respx.mock
async def test_success_passthrough(client: httpx.AsyncClient):
    tokens = {"access_token": "a", "refresh_token": "r", "token_type": "bearer", "expires_in": 900}
    respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(200, json=tokens))
    r = await client.post("/auth/login", json={"email": "a@b.dev", "password": "secret123"})
    assert r.status_code == 200
    assert r.json() == tokens


@respx.mock
async def test_auth_unreachable_returns_503(client: httpx.AsyncClient):
    respx.post(f"{AUTH}/login").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.post("/auth/login", json={})
    assert r.status_code == 503
    assert r.json()["error"] == "auth_unavailable"


@respx.mock
async def test_non_json_upstream_returns_502(client: httpx.AsyncClient):
    respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(500, text="Internal Server Error"))
    r = await client.post("/auth/login", json={})
    assert r.status_code == 502


@respx.mock
async def test_empty_204_passthrough(client: httpx.AsyncClient):
    respx.post(f"{AUTH}/logout").mock(return_value=httpx.Response(204))
    r = await client.post("/auth/logout", json={"refresh_token": "r"})
    assert r.status_code == 204
    assert r.content == b""


@respx.mock
async def test_get_me_forwards_authorization(client: httpx.AsyncClient):
    me = {"user_id": "u", "email": "a@b.dev", "username": "alice", "created_at": "2026-10-13T00:00:00Z"}
    route = respx.get(f"{AUTH}/me").mock(return_value=httpx.Response(200, json=me))
    r = await client.get("/auth/me", headers={"authorization": "Bearer abc"})
    assert r.status_code == 200
    assert r.json() == me
    assert route.calls.last.request.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_delete_me_forwards_token_and_password(client: httpx.AsyncClient):
    route = respx.post(f"{AUTH}/me/delete").mock(return_value=httpx.Response(204))
    r = await client.post("/auth/me/delete", json={"password": "secret123"}, headers={"authorization": "Bearer abc"})
    assert r.status_code == 204
    assert r.content == b""
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer abc"
    assert sent.content == b'{"password":"secret123"}'


@respx.mock
async def test_no_authorization_header_when_client_sends_none(client: httpx.AsyncClient):
    route = respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(401, json={"error": "x", "message": "y"}))
    await client.post("/auth/login", json={})
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_get_users_forwards_query_and_token(client: httpx.AsyncClient):
    users = {"users": [{"user_id": "u1", "username": "alice"}]}
    route = respx.get(f"{AUTH}/users").mock(return_value=httpx.Response(200, json=users))
    r = await client.get("/auth/users", params={"ids": "u1,u2"}, headers={"authorization": "Bearer abc"})
    assert r.status_code == 200
    assert r.json() == users
    sent = route.calls.last.request
    assert sent.url.params["ids"] == "u1,u2"
    assert sent.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_search_users_forwards_query_token_and_errors(client: httpx.AsyncClient):
    users = {"users": [{"user_id": "u1", "username": "Alice"}]}
    route = respx.get(f"{AUTH}/users/search").mock(return_value=httpx.Response(200, json=users))
    r = await client.get("/auth/users/search", params={"username": "ali"}, headers={"authorization": "Bearer abc"})
    assert r.status_code == 200
    assert r.json() == users
    sent = route.calls.last.request
    assert sent.url.params["username"] == "ali"
    assert sent.headers["authorization"] == "Bearer abc"

    limited = {"error": "rate_limited", "message": "Too many attempts, try again later."}
    route.mock(return_value=httpx.Response(429, json=limited))
    r = await client.get("/auth/users/search", params={"username": "bob"}, headers={"authorization": "Bearer abc"})
    assert r.status_code == 429
    assert r.json() == limited
