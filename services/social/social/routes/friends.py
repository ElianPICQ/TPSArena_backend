import uuid

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tpsarena_common.events import FriendAdded, FriendRemoved, FriendRequestRemoved, FriendRequestSent

from social import clients
from social.config import get_settings
from social.events import outbox
from social.deps import CurrentUserId, Session
from social.errors import APIError
from social.models import FriendRequest, Friendship, Player
from social.schemas import (
    ErrorResponse,
    Friend,
    FriendRequestResult,
    FriendsResponse,
    PendingRequest,
    UserIdRequest,
)

router = APIRouter(tags=["friends"])


async def ensure_players(session: AsyncSession, *user_ids: uuid.UUID) -> None:
    """Lazy player creation: Social adds a row the first time a user id shows up."""
    statement = insert(Player).values([{"user_id": u} for u in user_ids]).on_conflict_do_nothing()
    await session.execute(statement)


async def are_friends(session: AsyncSession, user_id: uuid.UUID, other_id: uuid.UUID) -> bool:
    return await session.get(Friendship, (user_id, other_id)) is not None


async def friend_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return await session.scalar(select(func.count()).select_from(Friendship).where(Friendship.user_id == user_id))


async def check_friend_limit(session: AsyncSession, *user_ids: uuid.UUID) -> None:
    limit = get_settings().max_friends
    for user_id in user_ids:
        if await friend_count(session, user_id) >= limit:
            raise APIError(409, "too_many_friends", f"A player can have at most {limit} friends.")


async def make_friends(session: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """Adds both rows and removes requests between the two (not committed)."""
    await session.execute(
        insert(Friendship).values([{"user_id": a, "friend_id": b}, {"user_id": b, "friend_id": a}]).on_conflict_do_nothing()
    )
    await session.execute(
        delete(FriendRequest).where(
            or_(
                (FriendRequest.from_user_id == a) & (FriendRequest.to_user_id == b),
                (FriendRequest.from_user_id == b) & (FriendRequest.to_user_id == a),
            )
        )
    )


@router.get("/friends", responses={401: {"model": ErrorResponse}})
async def list_friends(user_id: CurrentUserId, session: Session) -> FriendsResponse:
    friends = await session.scalars(
        select(Friendship).where(Friendship.user_id == user_id).order_by(Friendship.created_at)
    )
    requests = await session.scalars(
        select(FriendRequest)
        .where(or_(FriendRequest.from_user_id == user_id, FriendRequest.to_user_id == user_id))
        .order_by(FriendRequest.created_at)
    )
    incoming, outgoing = [], []
    for r in requests:
        if r.to_user_id == user_id:
            incoming.append(PendingRequest(request_id=r.id, user_id=r.from_user_id, created_at=r.created_at))
        else:
            outgoing.append(PendingRequest(request_id=r.id, user_id=r.to_user_id, created_at=r.created_at))
    return FriendsResponse(
        friends=[Friend(user_id=f.friend_id, since=f.created_at) for f in friends],
        incoming=incoming,
        outgoing=outgoing,
    )


@router.post(
    "/friends/requests",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def send_friend_request(
    body: UserIdRequest, request: Request, user_id: CurrentUserId, session: Session
) -> FriendRequestResult:
    target = body.user_id
    if target == user_id:
        raise APIError(400, "cannot_friend_self", "You can't add yourself as a friend.")
    if not await clients.user_exists(target, request.headers["authorization"]):
        raise APIError(404, "user_not_found", "This player doesn't exist.")

    await ensure_players(session, user_id, target)
    if await are_friends(session, user_id, target):
        raise APIError(409, "already_friends", "You are already friends.")
    await check_friend_limit(session, user_id, target)

    # They already asked you: accept instead of creating a second request.
    reverse = await session.scalar(
        select(FriendRequest).where(FriendRequest.from_user_id == target, FriendRequest.to_user_id == user_id)
    )
    if reverse is not None:
        await make_friends(session, user_id, target)
        await outbox.add(session, FriendAdded(user_id=user_id, friend_id=target))
        await session.commit()
        await outbox.try_flush()
        return FriendRequestResult(status="accepted", request_id=None)

    friend_request = FriendRequest(from_user_id=user_id, to_user_id=target)
    session.add(friend_request)
    try:
        await session.flush()
        await outbox.add(
            session, FriendRequestSent(request_id=friend_request.id, from_user_id=user_id, to_user_id=target)
        )
        await session.commit()
    except IntegrityError:
        # Lost a race with the same request sent twice at once.
        await session.rollback()
        raise APIError(409, "request_exists", "You already sent this player a request.")
    await outbox.try_flush()
    return FriendRequestResult(status="pending", request_id=friend_request.id)


@router.post(
    "/friends/requests/{request_id}/accept",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def accept_friend_request(request_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Friend:
    friend_request = await session.get(FriendRequest, request_id)
    if friend_request is None or friend_request.to_user_id != user_id:
        raise APIError(404, "request_not_found", "This friend request doesn't exist.")
    other = friend_request.from_user_id
    await check_friend_limit(session, user_id, other)
    await make_friends(session, user_id, other)
    await outbox.add(session, FriendAdded(user_id=user_id, friend_id=other))
    await session.commit()
    await outbox.try_flush()
    friendship = await session.get(Friendship, (user_id, other))
    return Friend(user_id=other, since=friendship.created_at)


@router.post(
    "/friends/requests/{request_id}/decline",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def decline_friend_request(request_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    """Declines a request you received, or cancels one you sent."""
    friend_request = await session.get(FriendRequest, request_id)
    if friend_request is None or user_id not in (friend_request.from_user_id, friend_request.to_user_id):
        raise APIError(404, "request_not_found", "This friend request doesn't exist.")
    await session.delete(friend_request)
    await outbox.add(
        session,
        FriendRequestRemoved(
            request_id=request_id, from_user_id=friend_request.from_user_id, to_user_id=friend_request.to_user_id
        ),
    )
    await session.commit()
    await outbox.try_flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: {"model": ErrorResponse}})
async def remove_friend(friend_id: uuid.UUID, user_id: CurrentUserId, session: Session) -> Response:
    result = await session.execute(
        delete(Friendship).where(
            or_(
                (Friendship.user_id == user_id) & (Friendship.friend_id == friend_id),
                (Friendship.user_id == friend_id) & (Friendship.friend_id == user_id),
            )
        )
    )
    if result.rowcount == 0:
        raise APIError(404, "not_friends", "You aren't friends with this player.")
    await outbox.add(session, FriendRemoved(user_id=user_id, friend_id=friend_id))
    await session.commit()
    await outbox.try_flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
