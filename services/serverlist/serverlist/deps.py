import hmac
import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from tpsarena_common.jwt_auth import JWKSVerifier

from serverlist.config import get_settings
from serverlist.errors import APIError

verifier = JWKSVerifier(get_settings().auth_jwks_url)
CurrentUserId = Annotated[uuid.UUID, Depends(verifier.current_user_id)]

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def _check_secret(credentials: HTTPAuthorizationCredentials | None, secret: str) -> None:
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), secret.encode()):
        raise APIError(401, "invalid_secret", "Missing or invalid secret.")


async def game_server(credentials: Credentials) -> None:
    _check_secret(credentials, get_settings().game_server_secret)


async def social_service(credentials: Credentials) -> None:
    _check_secret(credentials, get_settings().social_service_secret)


GameServer = Depends(game_server)
SocialService = Depends(social_service)
