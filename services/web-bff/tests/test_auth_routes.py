import json

import httpx
import respx

from web_bff.config import get_settings

AUTH = get_settings().auth_url
USER = {"user_id": "7b6f4a52-3c1e-4c55-9d2e-0a8b7f3e1d22", "email": "a@b.dev", "username": "alice", "created_at": "x"}
TOKENS = {"access_token": "access-1", "refresh_token": "refresh-1", "token_type": "bearer", "expires_in": 900}
COOKIE = {"cookie": "tpsarena_refresh=refresh-0"}


def set_cookie(r: httpx.Response) -> str:
    return r.headers["set-cookie"]


@respx.mock
async def test_login_puts_refresh_token_in_cookie(client):
    login = respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(200, json=TOKENS))
    me = respx.get(f"{AUTH}/me").mock(return_value=httpx.Response(200, json=USER))

    r = await client.post("/auth/login", json={"identifier": "alice", "password": "secret123"})

    assert r.status_code == 200
    assert r.json() == {
        "access_token": "access-1",
        "expires_in": 900,
        "user": {"user_id": USER["user_id"], "username": "alice"},
    }
    cookie = set_cookie(r)
    assert cookie.startswith("tpsarena_refresh=refresh-1;")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/auth", "Max-Age=2592000"):
        assert attribute in cookie
    assert r.headers["cache-control"] == "no-store"
    sent = login.calls.last.request
    assert json.loads(sent.content) == {"identifier": "alice", "password": "secret123"}
    assert sent.headers["x-forwarded-for"] == "203.0.113.7"
    assert me.calls.last.request.headers["authorization"] == "Bearer access-1"


@respx.mock
async def test_login_errors_pass_through_without_cookie(client):
    error = {"error": "email_not_verified", "message": "..."}
    respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(403, json=error))

    r = await client.post("/auth/login", json={"identifier": "alice", "password": "secret123"})

    assert (r.status_code, r.json()) == (403, error)
    assert "set-cookie" not in r.headers


@respx.mock
async def test_login_auth_down(client):
    respx.post(f"{AUTH}/login").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.post("/auth/login", json={"identifier": "alice", "password": "secret123"})
    assert (r.status_code, r.json()["error"]) == (503, "auth_unavailable")


@respx.mock
async def test_login_me_fails(client):
    respx.post(f"{AUTH}/login").mock(return_value=httpx.Response(200, json=TOKENS))
    respx.get(f"{AUTH}/me").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.post("/auth/login", json={"identifier": "alice", "password": "secret123"})
    assert (r.status_code, r.json()["error"]) == (503, "auth_unavailable")
    assert "set-cookie" not in r.headers


@respx.mock
async def test_refresh_rotates_cookie(client):
    rotated = {**TOKENS, "access_token": "access-2", "refresh_token": "refresh-2"}
    refresh = respx.post(f"{AUTH}/refresh").mock(return_value=httpx.Response(200, json=rotated))
    respx.get(f"{AUTH}/me").mock(return_value=httpx.Response(200, json=USER))

    r = await client.post("/auth/refresh", headers=COOKIE)

    assert r.json()["access_token"] == "access-2"
    assert json.loads(refresh.calls.last.request.content) == {"refresh_token": "refresh-0"}
    assert set_cookie(r).startswith("tpsarena_refresh=refresh-2;")


async def test_refresh_without_cookie(client):
    r = await client.post("/auth/refresh")
    assert (r.status_code, r.json()["error"]) == (401, "invalid_refresh_token")


@respx.mock
async def test_refresh_rejected_clears_cookie(client):
    error = {"error": "invalid_refresh_token", "message": "..."}
    respx.post(f"{AUTH}/refresh").mock(return_value=httpx.Response(401, json=error))

    r = await client.post("/auth/refresh", headers=COOKIE)

    assert (r.status_code, r.json()) == (401, error)
    assert set_cookie(r).startswith('tpsarena_refresh="";')
    assert "Max-Age=0" in set_cookie(r)


@respx.mock
async def test_refresh_auth_down_keeps_cookie(client):
    respx.post(f"{AUTH}/refresh").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.post("/auth/refresh", headers=COOKIE)
    assert (r.status_code, r.json()["error"]) == (503, "auth_unavailable")
    assert "set-cookie" not in r.headers


@respx.mock
async def test_logout_revokes_and_clears_cookie(client):
    logout = respx.post(f"{AUTH}/logout").mock(return_value=httpx.Response(204))

    r = await client.post("/auth/logout", headers=COOKIE)

    assert r.status_code == 204
    assert json.loads(logout.calls.last.request.content) == {"refresh_token": "refresh-0"}
    assert "Max-Age=0" in set_cookie(r)


@respx.mock
async def test_logout_without_cookie_or_auth(client):
    logout = respx.post(f"{AUTH}/logout").mock(side_effect=httpx.ConnectError("refused"))
    assert (await client.post("/auth/logout")).status_code == 204
    assert not logout.called
    assert (await client.post("/auth/logout", headers=COOKIE)).status_code == 204


async def test_other_services_are_not_exposed(client):
    for path in ("/auth/register", "/auth/me/delete", "/parties", "/servers", "/profiles/me"):
        assert (await client.post(path, json={})).status_code in (404, 405)
