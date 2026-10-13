import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select, tuple_

from stats.config import get_settings
from stats.deps import CurrentUserId, Session
from stats.errors import APIError
from stats.models import Match, MatchPlayer, PlayerAchievement
from stats.progression import ACHIEVEMENTS, level_for, xp_to_next
from stats.recording import player_stats
from stats.schemas import (
    AchievementResponse,
    ErrorResponse,
    PlayerMatch,
    PlayerMatchesResponse,
    PlayerStatsResponse,
    ProgressionResponse,
)

router = APIRouter(tags=["players"])


@router.get("/players/{user_id}/stats", responses={401: {"model": ErrorResponse}})
async def get_player_stats(user_id: uuid.UUID, _: CurrentUserId, session: Session) -> PlayerStatsResponse:
    """Zeros for a player who hasn't finished a match (Stats doesn't know which accounts exist)."""
    s = await player_stats(session, user_id)
    return PlayerStatsResponse(
        user_id=user_id,
        matches_played=s.matches_played,
        wins=s.wins,
        kills=s.kills,
        deaths=s.deaths,
        kd_ratio=round(s.kills / s.deaths, 2) if s.deaths else float(s.kills),
        level=s.level,
        xp=s.xp,
    )


@router.get("/progression/{user_id}", responses={401: {"model": ErrorResponse}})
async def get_progression(user_id: uuid.UUID, _: CurrentUserId, session: Session) -> ProgressionResponse:
    s = await player_stats(session, user_id)
    level, xp_in_level = level_for(s.xp)
    unlocked = {
        a.achievement_id: a.unlocked_at
        for a in await session.scalars(select(PlayerAchievement).where(PlayerAchievement.user_id == user_id))
    }
    return ProgressionResponse(
        user_id=user_id,
        level=level,
        xp=s.xp,
        xp_in_level=xp_in_level,
        xp_to_next_level=xp_to_next(level),
        achievements=[
            AchievementResponse(id=a.id, name=a.name, description=a.description, unlocked_at=unlocked.get(a.id))
            for a in ACHIEVEMENTS
        ],
    )


def encode_cursor(ended_at: datetime, match_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ended_at.isoformat()}|{match_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ended_at, match_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        parsed = datetime.fromisoformat(ended_at)
        if parsed.tzinfo is None:
            raise ValueError("cursor time without a timezone")
        return parsed, uuid.UUID(match_id)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise APIError(400, "invalid_cursor", "Use the next_cursor value from the previous page.")


@router.get(
    "/players/{user_id}/matches",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def get_player_matches(
    user_id: uuid.UUID,
    _: CurrentUserId,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=get_settings().match_history_page_max)] = 20,
    before: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
) -> PlayerMatchesResponse:
    """The player's matches, newest first.

    Paged with a cursor rather than an offset: matches finishing while the player scrolls
    would otherwise shift the pages and show the same match twice.
    """
    player_count = (
        select(func.count()).where(MatchPlayer.match_id == Match.match_id).correlate(Match).scalar_subquery()
    )
    query = (
        select(Match, MatchPlayer, player_count)
        .join(MatchPlayer, MatchPlayer.match_id == Match.match_id)
        .where(MatchPlayer.user_id == user_id)
        .order_by(Match.ended_at.desc(), Match.match_id.desc())
        .limit(limit + 1)  # one extra row tells whether there is a next page
    )
    if before is not None:
        query = query.where(tuple_(Match.ended_at, Match.match_id) < decode_cursor(before))

    rows = (await session.execute(query)).all()
    page = rows[:limit]
    matches = [
        PlayerMatch(
            match_id=match.match_id,
            map=match.map,
            started_at=match.started_at,
            ended_at=match.ended_at,
            kills=result.kills,
            deaths=result.deaths,
            placement=result.placement,
            player_count=count,
            xp_gained=result.xp_gained,
        )
        for match, result, count in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.ended_at, last.match_id)
    return PlayerMatchesResponse(matches=matches, next_cursor=next_cursor)
