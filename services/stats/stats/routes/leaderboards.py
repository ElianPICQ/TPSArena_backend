import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from stats import leaderboards
from stats.config import get_settings
from stats.deps import CurrentUserId, Session
from stats.leaderboards import LeaderboardType
from stats.schemas import ErrorResponse, LeaderboardAroundResponse, LeaderboardEntry, LeaderboardResponse

router = APIRouter(tags=["leaderboards"])


def as_entries(rows: list[tuple[str, int, int]]) -> list[LeaderboardEntry]:
    return [LeaderboardEntry(rank=rank, user_id=user_id, value=value) for user_id, value, rank in rows]


@router.get("/leaderboards/{type}", responses={401: {"model": ErrorResponse}})
async def get_leaderboard(
    type: LeaderboardType,
    _: CurrentUserId,
    session: Session,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=get_settings().leaderboard_page_max)] = 50,
) -> LeaderboardResponse:
    await leaderboards.ensure_built(session)
    rows = await leaderboards.entries(type, offset, offset + limit - 1)
    return LeaderboardResponse(type=type, total_players=await leaderboards.total(type), entries=as_entries(rows))


@router.get("/leaderboards/{type}/around/{user_id}", responses={401: {"model": ErrorResponse}})
async def get_leaderboard_around(
    type: LeaderboardType,
    user_id: uuid.UUID,
    _: CurrentUserId,
    session: Session,
    radius: Annotated[int, Query(ge=0, le=get_settings().leaderboard_radius_max)] = 5,
) -> LeaderboardAroundResponse:
    """The player's entry with up to `radius` players above and below."""
    await leaderboards.ensure_built(session)
    total = await leaderboards.total(type)
    position = await leaderboards.position(type, user_id)
    if position is None:
        return LeaderboardAroundResponse(type=type, total_players=total, player_rank=None, entries=[])
    rows = await leaderboards.entries(type, max(0, position - radius), position + radius)
    player_rank = next(rank for uid, _, rank in rows if uid == str(user_id))
    return LeaderboardAroundResponse(type=type, total_players=total, player_rank=player_rank, entries=as_entries(rows))
