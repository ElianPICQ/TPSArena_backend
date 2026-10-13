"""All-time leaderboards, cached in Redis sorted sets: leaderboard:{type} -> user_id scored by total.

PostgreSQL holds the truth. The worker writes absolute totals after each match (so handling an
event twice changes nothing), and the API rebuilds a board from PostgreSQL if Redis lost it.
Ranks are "1224" style: players with the same total share a rank.
"""

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stats.models import PlayerStats
from stats.redis_client import redis


class LeaderboardType(StrEnum):
    xp = "xp"
    wins = "wins"
    kills = "kills"


BUILT_MARKER = "leaderboards:built"


def board_key(board: LeaderboardType) -> str:
    return f"leaderboard:{board}"


async def update_player(stats: PlayerStats) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        for board in LeaderboardType:
            # GT: totals only grow, so a late write with an older total can't lower the score.
            pipe.zadd(board_key(board), {str(stats.user_id): getattr(stats, board.value)}, gt=True)
        await pipe.execute()


async def remove_player(user_id: uuid.UUID) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        for board in LeaderboardType:
            pipe.zrem(board_key(board), str(user_id))
        await pipe.execute()


async def ensure_built(session: AsyncSession) -> None:
    """Rebuild every board from PostgreSQL if the cache is gone (e.g. Redis data lost)."""
    if await redis.exists(BUILT_MARKER):
        return
    rows = (await session.execute(select(PlayerStats))).scalars().all()
    async with redis.pipeline(transaction=True) as pipe:
        for board in LeaderboardType:
            tmp = f"{board_key(board)}:rebuild"
            pipe.delete(tmp)
            if rows:
                pipe.zadd(tmp, {str(r.user_id): getattr(r, board.value) for r in rows})
                pipe.rename(tmp, board_key(board))
            else:
                pipe.delete(board_key(board))
        pipe.set(BUILT_MARKER, 1)
        await pipe.execute()


async def rank_of(board: LeaderboardType, score: float) -> int:
    """1 + number of players with a strictly higher total."""
    return await redis.zcount(board_key(board), f"({score}", "+inf") + 1


async def entries(board: LeaderboardType, start: int, stop: int) -> list[tuple[str, int, int]]:
    """(user_id, value, rank) for positions start..stop (0-based, inclusive), best first."""
    rows = await redis.zrevrange(board_key(board), start, stop, withscores=True)
    return [(user_id, int(score), await rank_of(board, score)) for user_id, score in rows]


async def total(board: LeaderboardType) -> int:
    return await redis.zcard(board_key(board))


async def position(board: LeaderboardType, user_id: uuid.UUID) -> int | None:
    """0-based position in the board, or None if the player has no entry."""
    return await redis.zrevrank(board_key(board), str(user_id))
