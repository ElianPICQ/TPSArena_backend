import uuid

from fastapi import APIRouter
from sqlalchemy import select

from stats.deps import CurrentUserId, Session
from stats.errors import APIError
from stats.models import Match, MatchPlayer
from stats.schemas import ErrorResponse, MatchPlayerResponse, MatchResponse

router = APIRouter(tags=["matches"])


@router.get("/matches/{match_id}", responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def get_match(match_id: uuid.UUID, _: CurrentUserId, session: Session) -> MatchResponse:
    match = await session.get(Match, match_id)
    if match is None:
        raise APIError(404, "match_not_found", "This match doesn't exist.")
    players = await session.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match_id).order_by(MatchPlayer.placement, MatchPlayer.id)
    )
    return MatchResponse(
        match_id=match.match_id,
        map=match.map,
        started_at=match.started_at,
        ended_at=match.ended_at,
        players=[
            MatchPlayerResponse(
                user_id=p.user_id, kills=p.kills, deaths=p.deaths, placement=p.placement, xp_gained=p.xp_gained
            )
            for p in players
        ],
    )
