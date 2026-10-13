import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from auth import security
from auth.cleanup import cleanup
from auth.db import SessionLocal
from auth.models import EmailCode, RefreshToken, User


def new_user(age: timedelta, verified: bool) -> User:
    suffix = uuid.uuid4().hex[:10]
    created = datetime.now(UTC) - age
    return User(
        email=f"c_{suffix}@example.dev",
        username=f"c_{suffix}",
        password_hash="x",
        email_verified_at=created if verified else None,
        created_at=created,
    )


def token(user: User, expires_in: timedelta, revoked: bool = False, rotated: bool = False) -> RefreshToken:
    now = datetime.now(UTC)
    return RefreshToken(
        user_id=user.id,
        token_hash=security.hash_refresh_token(uuid.uuid4().hex),
        expires_at=now + expires_in,
        revoked_at=now if revoked or rotated else None,
        rotated_at=now if rotated else None,
    )


def code(user: User, expires_in: timedelta) -> EmailCode:
    return EmailCode(user_id=user.id, purpose="verify", code_hash="x", expires_at=datetime.now(UTC) + expires_in)


async def test_cleanup_deletes_only_what_is_useless():
    async with SessionLocal() as session:
        abandoned = new_user(timedelta(days=8), verified=False)
        recent = new_user(timedelta(days=6), verified=False)
        player = new_user(timedelta(days=30), verified=True)
        session.add_all([abandoned, recent, player])
        await session.flush()
        kept = [
            token(player, timedelta(days=1)),  # in use
            token(player, timedelta(days=1), rotated=True),  # kept to catch a stolen token
            code(recent, timedelta(minutes=5)),
        ]
        gone = [
            token(player, -timedelta(seconds=1)),  # expired
            token(player, timedelta(days=1), revoked=True),  # logged out
            code(player, -timedelta(seconds=1)),
            code(abandoned, timedelta(minutes=5)),  # goes with its user
        ]
        session.add_all(kept + gone)
        await session.commit()
        ids = {"kept": [x.id for x in kept], "gone": [x.id for x in gone]}

        cleaned = await cleanup(session)

        assert cleaned.users >= 1 and cleaned.refresh_tokens >= 2 and cleaned.email_codes >= 1
        user_ids = set(await session.scalars(select(User.id)))
        assert abandoned.id not in user_ids
        assert {recent.id, player.id} <= user_ids
        remaining = set(await session.scalars(select(RefreshToken.id))) | set(await session.scalars(select(EmailCode.id)))
        assert set(ids["kept"]) <= remaining
        assert not set(ids["gone"]) & remaining
