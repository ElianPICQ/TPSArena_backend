"""Login, refresh and logout for the PWA.

Auth returns both tokens in the body. Here the refresh token goes into an HttpOnly cookie
instead, so a script injected into the page can't steal it; the access token (15 min) is
returned in the body and kept in memory by the PWA. SameSite=Strict keeps other sites from
sending the cookie, which is what protects /refresh and /logout from cross-site requests.
"""

import logging

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from web_bff import upstream
from web_bff.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=settings.refresh_cookie_max_age_days * 86400,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def no_store(response: Response) -> Response:
    response.headers["cache-control"] = "no-store"
    return response


async def session_response(request: Request, tokens: dict) -> Response:
    """The PWA's session: access token + who is logged in. The refresh token goes in the cookie."""
    client: httpx.AsyncClient = request.app.state.auth_client
    try:
        me = await client.get("/me", headers={"authorization": f"Bearer {tokens['access_token']}"})
    except httpx.RequestError as exc:
        log.warning("auth unreachable on /me: %s", exc)
        me = None
    if me is None or not me.is_success:
        # The tokens are valid but unusable without the user id: the PWA retries the login.
        return no_store(upstream.unavailable("auth"))
    user = me.json()
    response = JSONResponse(
        content={
            "access_token": tokens["access_token"],
            "expires_in": tokens["expires_in"],
            "user": {"user_id": user["user_id"], "username": user["username"]},
        }
    )
    set_refresh_cookie(response, tokens["refresh_token"])
    return no_store(response)


@router.post("/login")
async def login(request: Request) -> Response:
    """Body: identifier (email or username), password. Same errors as Auth's /login."""
    answer = await upstream.send(request, request.app.state.auth_client, "auth", "/login", "POST")
    if isinstance(answer, Response):
        return no_store(answer)
    if not answer.is_success:
        return no_store(upstream.relay(answer))
    return await session_response(request, answer.json())


@router.post("/refresh")
async def refresh(request: Request) -> Response:
    """New access token from the cookie. 401 invalid_refresh_token: log in again."""
    token = request.cookies.get(get_settings().refresh_cookie_name)
    if not token:
        return no_store(
            JSONResponse(
                status_code=401,
                content={"error": "invalid_refresh_token", "message": "The refresh token is invalid or has expired."},
            )
        )
    client: httpx.AsyncClient = request.app.state.auth_client
    try:
        answer = await client.post("/refresh", json={"refresh_token": token})
    except httpx.RequestError as exc:
        log.warning("auth unreachable on /refresh: %s", exc)
        return no_store(upstream.unavailable("auth"))
    if answer.status_code == 401:
        # Expired, logged out or revoked: the cookie is useless from now on.
        response = upstream.relay(answer)
        clear_refresh_cookie(response)
        return no_store(response)
    if not answer.is_success:
        return no_store(upstream.relay(answer))
    return await session_response(request, answer.json())


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    """Revokes the refresh token and removes the cookie. Always 204, like Auth's /logout."""
    token = request.cookies.get(get_settings().refresh_cookie_name)
    if token:
        client: httpx.AsyncClient = request.app.state.auth_client
        try:
            await client.post("/logout", json={"refresh_token": token})
        except httpx.RequestError as exc:
            # The cookie is still removed: this browser can't use the token any more,
            # and it expires on its own.
            log.warning("auth unreachable on /logout: %s", exc)
    response = Response(status_code=204)
    clear_refresh_cookie(response)
    return no_store(response)
