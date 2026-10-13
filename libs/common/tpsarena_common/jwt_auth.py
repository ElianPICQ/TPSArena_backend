"""Check player access tokens with Auth's public keys (JWKS), without calling Auth per request.

Usage in a service:

    verifier = JWKSVerifier("http://auth:8001/.well-known/jwks.json")
    CurrentUserId = Annotated[uuid.UUID, Depends(verifier.current_user_id)]

Error codes match Auth's own: not_authenticated, token_expired, invalid_token.
"""

import asyncio
import logging
import time
import uuid

import httpx
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

log = logging.getLogger(__name__)

bearer = HTTPBearer(auto_error=False)


class TokenError(Exception):
    """Raised by the dependency; turn it into a response with `token_error_handler`."""

    def __init__(self, error: str, message: str):
        self.error = error
        self.message = message


async def token_error_handler(_: Request, exc: TokenError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": exc.error, "message": exc.message})


class JWKSVerifier:
    def __init__(self, jwks_url: str, refetch_cooldown_seconds: float = 30.0, timeout_seconds: float = 5.0):
        self.jwks_url = jwks_url
        self.refetch_cooldown_seconds = refetch_cooldown_seconds
        self.timeout_seconds = timeout_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._last_fetch = float("-inf")
        self._lock = asyncio.Lock()

    def load_jwks(self, jwks: dict) -> None:
        keys = {}
        for data in jwks.get("keys", []):
            try:
                keys[data["kid"]] = jwt.PyJWK(data)
            except (KeyError, jwt.PyJWKError) as exc:
                log.warning("Skipping unusable JWK %s: %s", data.get("kid"), exc)
        self._keys = keys

    async def _refresh(self) -> None:
        # One fetch at a time, and at most one per cooldown: tokens with a made-up kid
        # must not make every request hit Auth.
        async with self._lock:
            if time.monotonic() - self._last_fetch < self.refetch_cooldown_seconds:
                return
            self._last_fetch = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(self.jwks_url)
                    response.raise_for_status()
                    self.load_jwks(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Could not fetch JWKS from %s: %s", self.jwks_url, exc)

    async def _key_for(self, kid: str) -> jwt.PyJWK | None:
        # An unknown kid usually means Auth rotated its key: fetch the set again.
        if kid not in self._keys:
            await self._refresh()
        return self._keys.get(kid)

    async def decode(self, token: str) -> uuid.UUID:
        """Return the user id in a valid access token. Raises TokenError otherwise."""
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError:
            raise TokenError("invalid_token", "The access token is invalid.")
        key = await self._key_for(kid) if isinstance(kid, str) else None
        if key is None:
            raise TokenError("invalid_token", "The access token is invalid.")
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["EdDSA"],  # never let the token pick its own algorithm
                options={"require": ["exp", "sub"]},
            )
            return uuid.UUID(claims["sub"])
        except jwt.ExpiredSignatureError:
            raise TokenError("token_expired", "The access token has expired.")
        except (jwt.InvalidTokenError, ValueError):
            raise TokenError("invalid_token", "The access token is invalid.")

    async def current_user_id(self, request: Request) -> uuid.UUID:
        """FastAPI dependency: the id of the player calling the route."""
        credentials = await bearer(request)
        if credentials is None:
            raise TokenError("not_authenticated", "Missing access token.")
        return await self.decode(credentials.credentials)
