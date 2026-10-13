"""Periodic cleanup, run by the Auth worker (one instance at a time, through a Redis lock):

- unverified accounts older than 7 days: they never received a token, so no other service has
  data about them and no user.deleted is needed; their email and username become free again
- refresh tokens that expired, or were revoked without being rotated (logout, password reset).
  Rotated tokens stay until they expire: using one again is how a stolen token is detected
- email codes that expired (used or not)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from auth.config import get_settings
from auth.db import SessionLocal
from auth.models import EmailCode, RefreshToken, User
from auth.redis_client import redis

log = logging.getLogger(__name__)

LOCK_KEY = "auth:cleanup:lock"


@dataclass
class Cleaned:
    users: int
    refresh_tokens: int
    email_codes: int


async def cleanup(session: AsyncSession, now: datetime | None = None) -> Cleaned:
    settings = get_settings()
    now = now or datetime.now(UTC)
    users = await session.execute(
        delete(User).where(
            User.email_verified_at.is_(None),
            User.created_at < now - timedelta(days=settings.unverified_account_ttl_days),
        )
    )
    tokens = await session.execute(
        delete(RefreshToken).where(
            or_(
                RefreshToken.expires_at < now,
                RefreshToken.revoked_at.is_not(None) & RefreshToken.rotated_at.is_(None),
            )
        )
    )
    codes = await session.execute(delete(EmailCode).where(EmailCode.expires_at < now))
    await session.commit()
    return Cleaned(users.rowcount, tokens.rowcount, codes.rowcount)


async def run() -> None:
    """Forever: once per interval, on whichever worker instance takes the lock first."""
    interval = get_settings().cleanup_interval_seconds
    while True:
        try:
            if await redis.set(LOCK_KEY, 1, nx=True, ex=max(interval - 60, 1)):
                async with SessionLocal() as session:
                    cleaned = await cleanup(session)
                log.info(
                    "Cleanup: %d unverified accounts, %d refresh tokens, %d email codes deleted",
                    cleaned.users,
                    cleaned.refresh_tokens,
                    cleaned.email_codes,
                )
        except Exception:
            log.exception("Cleanup failed, retrying next time")
        await asyncio.sleep(min(interval, 300))
