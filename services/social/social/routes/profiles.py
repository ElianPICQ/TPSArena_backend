import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy.dialects.postgresql import insert

from social.deps import CurrentUserId, Session
from social.models import Player
from social.schemas import ErrorResponse, ProfileResponse, ProfileUpdate

router = APIRouter(tags=["profiles"])


@router.get("/profiles/{user_id}", responses={401: {"model": ErrorResponse}})
async def get_profile(user_id: uuid.UUID, _: CurrentUserId, session: Session) -> ProfileResponse:
    """A player who never edited their profile gets the defaults.

    Social doesn't know which accounts exist: the BFF returns 404 when Auth has no name for the id.
    """
    player = await session.get(Player, user_id)
    if player is None:
        return ProfileResponse(user_id=user_id, bio="", avatar="default")
    return ProfileResponse(user_id=player.user_id, bio=player.bio, avatar=player.avatar)


@router.patch("/profiles/me", responses={401: {"model": ErrorResponse}})
async def update_my_profile(body: ProfileUpdate, user_id: CurrentUserId, session: Session) -> ProfileResponse:
    changes = body.model_dump(exclude_none=True)
    statement = insert(Player).values(user_id=user_id, **changes)
    statement = statement.on_conflict_do_update(
        index_elements=[Player.user_id], set_={**changes, "updated_at": datetime.now(UTC)}
    ).returning(Player)
    player = (await session.execute(statement)).scalar_one()
    await session.commit()
    return ProfileResponse(user_id=player.user_id, bio=player.bio, avatar=player.avatar)
