import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import ratelimit, security
from auth.config import get_settings
from auth.db import get_session
from auth.deps import CurrentUserId
from auth.errors import APIError
from auth.events import publish_email, publish_user_deleted
from auth.models import EmailCode, RefreshToken, User
from auth.schemas import (
    DeleteAccountRequest,
    EmailRequest,
    ErrorResponse,
    LoginRequest,
    MeResponse,
    MessageResponse,
    PasswordResetConfirmRequest,
    PublicUser,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UsersResponse,
    VerifyEmailRequest,
)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    # Auth is only reachable through the BFF, which sets X-Forwarded-For to the player's IP.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    settings = get_settings()
    refresh_token = security.generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=security.hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    await session.commit()
    return TokenResponse(
        access_token=security.create_access_token(user.id),
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_seconds,
    )


async def new_email_code(session: AsyncSession, user_id: uuid.UUID, purpose: str) -> str:
    """Add a fresh code (not committed) and invalidate the user's previous unused codes for that purpose."""
    now = datetime.now(UTC)
    await session.execute(
        update(EmailCode)
        .where(EmailCode.user_id == user_id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .values(used_at=now)
    )
    code = security.generate_code()
    session.add(
        EmailCode(
            user_id=user_id,
            purpose=purpose,
            code_hash=security.hash_code(code),
            expires_at=now + timedelta(minutes=get_settings().code_ttl_minutes),
        )
    )
    return code


def invalid_code() -> APIError:
    return APIError(400, "invalid_code", "The code is invalid or has expired.")


async def consume_email_code(session: AsyncSession, user_id: uuid.UUID, purpose: str, code: str) -> datetime:
    """Check `code` against the user's latest unused code for `purpose` and mark it used (not committed).

    Raises invalid_code() if there is none, it expired, it ran out of attempts, or it doesn't match
    (a mismatch uses up an attempt, committed right away). Returns the time it was used.
    """
    settings = get_settings()
    email_code = await session.scalar(
        select(EmailCode)
        .where(EmailCode.user_id == user_id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .order_by(EmailCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if email_code is None or email_code.expires_at <= now or email_code.attempts >= settings.code_max_attempts:
        raise invalid_code()

    if not security.code_matches(code, email_code.code_hash):
        email_code.attempts += 1
        await session.commit()
        raise invalid_code()

    email_code.used_at = now
    return now


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
)
async def register(body: RegisterRequest, session: Session) -> RegisterResponse:
    if await session.scalar(select(User.id).where(User.email == body.email)):
        raise APIError(409, "email_taken", "This email is already registered.")
    if await session.scalar(select(User.id).where(func.lower(User.username) == body.username.lower())):
        raise APIError(409, "username_taken", "This username is already taken.")

    user = User(email=body.email, username=body.username, password_hash=security.hash_password(body.password))
    session.add(user)
    try:
        await session.flush()
        code = await new_email_code(session, user.id, "verify")
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent registration using the same email or username.
        await session.rollback()
        raise APIError(409, "already_registered", "This email or username is already taken.")

    await publish_email("verify", user.email, user.username, code)
    return RegisterResponse(user_id=user.id)


@router.post(
    "/verify-email",
    responses={400: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def verify_email(body: VerifyEmailRequest, request: Request, session: Session) -> TokenResponse:
    settings = get_settings()
    window = settings.code_ttl_minutes * 60
    await ratelimit.hit("verify_ip", client_ip(request), settings.verify_rate_limit, window)
    await ratelimit.hit("verify_email", body.email, settings.verify_rate_limit, window)

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or user.email_verified_at is not None:
        raise invalid_code()

    now = await consume_email_code(session, user.id, "verify", body.code)
    user.email_verified_at = now
    return await issue_tokens(session, user)


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: {"model": ErrorResponse}},
)
async def resend_verification_code(body: EmailRequest, request: Request, session: Session) -> MessageResponse:
    settings = get_settings()
    window = settings.code_ttl_minutes * 60
    # Counted whether or not the account exists, so a 429 doesn't reveal anything either.
    await ratelimit.hit("resend_ip", client_ip(request), settings.resend_ip_rate_limit, window)
    await ratelimit.hit("resend_email", body.email, settings.resend_email_rate_limit, window)

    # Same answer whether the account exists, is already verified, or not.
    answer = MessageResponse(message="If an unverified account exists for this email, a new code was sent.")

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or user.email_verified_at is not None:
        return answer

    code = await new_email_code(session, user.id, "verify")
    await session.commit()
    await publish_email("verify", user.email, user.username, code)
    return answer


@router.post(
    "/login",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def login(body: LoginRequest, request: Request, session: Session) -> TokenResponse:
    settings = get_settings()
    await ratelimit.hit("login_ip", client_ip(request), settings.login_rate_limit, 60)
    await ratelimit.hit("login_email", body.email, settings.login_rate_limit, 60)

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        security.verify_password(body.password, security.DUMMY_PASSWORD_HASH)
        raise APIError(401, "invalid_credentials", "Invalid email or password.")
    if not security.verify_password(body.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Invalid email or password.")
    # Only revealed after a correct password, so it doesn't tell which emails are registered.
    if user.email_verified_at is None:
        raise APIError(403, "email_not_verified", "Verify your email address before logging in.")

    return await issue_tokens(session, user)


@router.post("/refresh", responses={401: {"model": ErrorResponse}})
async def refresh(body: RefreshTokenRequest, session: Session) -> TokenResponse:
    invalid = APIError(401, "invalid_refresh_token", "The refresh token is invalid or has expired.")

    # Row lock: two concurrent refreshes with the same token can't both rotate it.
    token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_refresh_token(body.refresh_token))
        .with_for_update()
    )
    if token is None:
        raise invalid

    now = datetime.now(UTC)
    if token.revoked_at is not None:
        # A rotated token used again means it may have been stolen: revoke every session of that user.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise invalid
    if token.expires_at <= now:
        raise invalid

    user = await session.get(User, token.user_id)
    if user is None:
        raise invalid

    token.revoked_at = now
    return await issue_tokens(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshTokenRequest, session: Session) -> Response:
    # Idempotent: an unknown or already revoked token gives the same answer.
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == security.hash_refresh_token(body.refresh_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: {"model": ErrorResponse}},
)
async def request_password_reset(body: EmailRequest, request: Request, session: Session) -> MessageResponse:
    settings = get_settings()
    window = settings.code_ttl_minutes * 60
    # Counted whether or not the account exists, so a 429 doesn't reveal anything either.
    await ratelimit.hit("reset_request_ip", client_ip(request), settings.resend_ip_rate_limit, window)
    await ratelimit.hit("reset_request_email", body.email, settings.resend_email_rate_limit, window)

    # Never reveals whether the email is registered.
    answer = MessageResponse(message="If an account exists for this email, a code was sent.")

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        return answer

    code = await new_email_code(session, user.id, "reset")
    await session.commit()
    await publish_email("reset", user.email, user.username, code)
    return answer


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def confirm_password_reset(body: PasswordResetConfirmRequest, request: Request, session: Session) -> Response:
    settings = get_settings()
    window = settings.code_ttl_minutes * 60
    await ratelimit.hit("reset_confirm_ip", client_ip(request), settings.verify_rate_limit, window)
    await ratelimit.hit("reset_confirm_email", body.email, settings.verify_rate_limit, window)

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise invalid_code()

    now = await consume_email_code(session, user.id, "reset", body.code)
    user.password_hash = security.hash_password(body.new_password)
    # The code reached this inbox, which proves the player owns the email address.
    if user.email_verified_at is None:
        user.email_verified_at = now
    # Log out every device: the player logs in again with the new password.
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def load_current_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        # Valid token, but the account was deleted after the token was issued.
        raise APIError(401, "invalid_token", "The access token is invalid.")
    return user


@router.get("/me", responses={401: {"model": ErrorResponse}})
async def get_me(user_id: CurrentUserId, session: Session) -> MeResponse:
    user = await load_current_user(session, user_id)
    return MeResponse(user_id=user.id, email=user.email, username=user.username, created_at=user.created_at)


# POST rather than DELETE /me: the password goes in a body, and some proxies drop bodies on DELETE.
@router.post(
    "/me/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def delete_me(body: DeleteAccountRequest, user_id: CurrentUserId, session: Session) -> Response:
    settings = get_settings()
    await ratelimit.hit("delete_account", str(user_id), settings.delete_account_rate_limit, 15 * 60)

    user = await load_current_user(session, user_id)
    # 403, not 401: the token is fine, so the client must not treat this as "log in again".
    if not security.verify_password(body.password, user.password_hash):
        raise APIError(403, "wrong_password", "The password is incorrect.")

    await session.delete(user)  # refresh_tokens and email_codes go too (ON DELETE CASCADE)
    await session.commit()
    # TODO.md: publish through an outbox so this event can't be lost if Redis fails after the commit.
    await publish_user_deleted(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}})
async def get_users(
    _: CurrentUserId,
    session: Session,
    ids: Annotated[str, Query(description="Comma-separated user ids")],
) -> UsersResponse:
    """Batch name lookup, e.g. for leaderboards and friend lists. Only public fields, never the email.

    Unknown ids are left out of the answer, so a deleted player simply has no name.
    """
    settings = get_settings()
    try:
        user_ids = {uuid.UUID(part.strip()) for part in ids.split(",") if part.strip()}
    except ValueError:
        raise APIError(400, "invalid_ids", "ids must be a comma-separated list of user ids.")
    if not user_ids:
        return UsersResponse(users=[])
    if len(user_ids) > settings.users_lookup_max_ids:
        raise APIError(400, "too_many_ids", f"At most {settings.users_lookup_max_ids} ids per request.")

    rows = await session.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
    return UsersResponse(users=[PublicUser(user_id=row.id, username=row.username) for row in rows])
