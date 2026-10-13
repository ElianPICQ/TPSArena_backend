import uuid
from datetime import datetime

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    message: str


class MatchPlayerResponse(BaseModel):
    user_id: uuid.UUID | None  # null: the account was deleted
    kills: int
    deaths: int
    placement: int
    xp_gained: int


class MatchResponse(BaseModel):
    match_id: uuid.UUID
    map: str
    started_at: datetime
    ended_at: datetime
    players: list[MatchPlayerResponse]  # by placement


class PlayerStatsResponse(BaseModel):
    user_id: uuid.UUID
    matches_played: int
    wins: int
    kills: int
    deaths: int
    kd_ratio: float  # kills / deaths, or kills when the player never died
    level: int
    xp: int


class PlayerMatch(BaseModel):
    """One match from a player's point of view."""

    match_id: uuid.UUID
    map: str
    started_at: datetime
    ended_at: datetime
    kills: int
    deaths: int
    placement: int
    player_count: int
    xp_gained: int


class PlayerMatchesResponse(BaseModel):
    matches: list[PlayerMatch]  # newest first
    next_cursor: str | None  # pass as `before` for the next page; null on the last page


class LeaderboardEntry(BaseModel):
    rank: int  # players with the same value share a rank
    user_id: uuid.UUID
    value: int


class LeaderboardResponse(BaseModel):
    type: str
    total_players: int
    entries: list[LeaderboardEntry]


class LeaderboardAroundResponse(BaseModel):
    type: str
    total_players: int
    player_rank: int | None  # null: the player hasn't finished a match yet
    entries: list[LeaderboardEntry]


class AchievementResponse(BaseModel):
    id: str
    name: str
    description: str
    unlocked_at: datetime | None


class ProgressionResponse(BaseModel):
    user_id: uuid.UUID
    level: int
    xp: int
    xp_in_level: int
    xp_to_next_level: int
    achievements: list[AchievementResponse]  # every achievement; unlocked_at null if locked
