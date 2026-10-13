import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from tpsarena_common.jwt_auth import JWKSVerifier

from stats.config import get_settings
from stats.db import get_session

verifier = JWKSVerifier(get_settings().auth_jwks_url)
CurrentUserId = Annotated[uuid.UUID, Depends(verifier.current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
