import hashlib
import hmac
import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from tpsarena_common.jwt_auth import JWKSVerifier

from serverlist.config import get_settings
from serverlist.errors import APIError
from serverlist.redis_client import redis, server_key

verifier = JWKSVerifier(get_settings().auth_jwks_url)
CurrentUserId = Annotated[uuid.UUID, Depends(verifier.current_user_id)]

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def _check_secret(credentials: HTTPAuthorizationCredentials | None, secret: str) -> None:
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), secret.encode()):
        raise APIError(401, "invalid_secret", "Missing or invalid secret.")


async def game_server(credentials: Credentials) -> None:
    """Registration only: the shared secret every game server is deployed with."""
    _check_secret(credentials, get_settings().game_server_secret)


def hash_server_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def check_server_token(server_id: str, credentials: HTTPAuthorizationCredentials | None) -> bool:
    """Every other game server call: the token this server got when it registered.

    Returns False if the server isn't registered (any more); raises 401 for a wrong token,
    so one server can't act as another.
    """
    stored = await redis.hget(server_key(server_id), "token_hash")
    if stored is None:
        return False
    if credentials is None or not hmac.compare_digest(hash_server_token(credentials.credentials), stored):
        raise APIError(401, "invalid_server_token", "Missing or invalid server token.")
    return True


async def social_service(credentials: Credentials) -> None:
    _check_secret(credentials, get_settings().social_service_secret)


GameServer = Depends(game_server)
SocialService = Depends(social_service)
