import uuid
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth import security
from auth.errors import APIError

bearer = HTTPBearer(auto_error=False)  # we raise our own error format instead of FastAPI's


async def current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> uuid.UUID:
    if credentials is None:
        raise APIError(401, "not_authenticated", "Missing access token.")
    try:
        return security.decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise APIError(401, "token_expired", "The access token has expired.")
    except (jwt.InvalidTokenError, ValueError):
        raise APIError(401, "invalid_token", "The access token is invalid.")


CurrentUserId = Annotated[uuid.UUID, Depends(current_user_id)]
