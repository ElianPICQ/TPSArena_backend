"""Writing match results and deleting a player's data. Used by the worker."""

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tpsarena_common.events import AchievementUnlocked, Event, LevelUp, MatchFinished

from stats import leaderboards
from stats.models import Match, MatchPlayer, PlayerAchievement, PlayerStats
from stats.progression import ACHIEVEMENTS, level_for, xp_for


async def record_match(session: AsyncSession, event: MatchFinished) -> list[Event]:
    """Store the match and update every player's totals, in one transaction.

    Idempotent by match_id: a match already stored is skipped. Returns the progress events
    (level ups, achievements) to publish once the transaction is committed.
    """
    inserted = await session.scalar(
        insert(Match)
        .values(
            match_id=event.match_id,
            server_id=event.server_id,
            party_id=event.party_id,
            map=event.map,
            started_at=event.started_at,
            ended_at=event.ended_at,
        )
        .on_conflict_do_nothing()
        .returning(Match.match_id)
    )
    if inserted is None:
        return []

    progress: list[Event] = []
    updated: list[PlayerStats] = []
    for result in event.players:
        xp = xp_for(result)
        session.add(
            MatchPlayer(
                match_id=event.match_id,
                user_id=result.user_id,
                kills=result.kills,
                deaths=result.deaths,
                placement=result.placement,
                xp_gained=xp,
            )
        )
        won = int(result.placement == 1)
        # Increment in SQL: two matches of the same player recorded at once both count.
        statement = insert(PlayerStats).values(
            user_id=result.user_id, matches_played=1, wins=won, kills=result.kills, deaths=result.deaths, xp=xp
        )
        statement = statement.on_conflict_do_update(
            index_elements=[PlayerStats.user_id],
            set_={
                "matches_played": PlayerStats.matches_played + 1,
                "wins": PlayerStats.wins + won,
                "kills": PlayerStats.kills + result.kills,
                "deaths": PlayerStats.deaths + result.deaths,
                "xp": PlayerStats.xp + xp,
                "updated_at": func.now(),
            },
        ).returning(PlayerStats)
        stats = (await session.execute(statement)).scalar_one()

        new_level, _ = level_for(stats.xp)
        if new_level > stats.level:  # RETURNING gave the level before this match
            await session.execute(
                update(PlayerStats).where(PlayerStats.user_id == result.user_id).values(level=new_level)
            )
            stats.level = new_level
            progress.append(LevelUp(user_id=result.user_id, level=new_level, match_id=event.match_id))

        for achievement in ACHIEVEMENTS:
            if achievement.unlocked(stats, result):
                unlocked = await session.scalar(
                    insert(PlayerAchievement)
                    .values(user_id=result.user_id, achievement_id=achievement.id, match_id=event.match_id)
                    .on_conflict_do_nothing()
                    .returning(PlayerAchievement.achievement_id)
                )
                if unlocked:
                    progress.append(
                        AchievementUnlocked(user_id=result.user_id, achievement_id=achievement.id, match_id=event.match_id)
                    )
        updated.append(stats)

    await session.commit()
    for stats in updated:
        await leaderboards.update_player(stats)
    return progress


async def delete_player(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Totals and achievements are deleted; match rows stay, without the user id."""
    await session.execute(update(MatchPlayer).where(MatchPlayer.user_id == user_id).values(user_id=None))
    await session.execute(delete(PlayerStats).where(PlayerStats.user_id == user_id))
    await session.commit()
    await leaderboards.remove_player(user_id)


async def player_stats(session: AsyncSession, user_id: uuid.UUID) -> PlayerStats:
    """A player's totals; zeros if they never finished a match."""
    stats = await session.scalar(select(PlayerStats).where(PlayerStats.user_id == user_id))
    return stats or PlayerStats(user_id=user_id, matches_played=0, wins=0, kills=0, deaths=0, xp=0, level=1)
