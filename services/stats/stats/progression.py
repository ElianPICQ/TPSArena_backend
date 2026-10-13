"""XP, levels and achievements. Rules live in code: changing them doesn't need a migration."""

from collections.abc import Callable
from dataclasses import dataclass

from tpsarena_common.events import MatchPlayerResult

from stats.config import get_settings
from stats.models import PlayerStats


def xp_for(result: MatchPlayerResult) -> int:
    settings = get_settings()
    xp = settings.xp_per_match + settings.xp_per_kill * result.kills
    if result.placement == 1:
        xp += settings.xp_for_win
    return xp


def xp_to_next(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    settings = get_settings()
    return settings.level_base_xp + settings.level_step_xp * (level - 1)


def level_for(xp: int) -> tuple[int, int]:
    """(level, XP earned inside that level). Everyone starts at level 1 with 0 XP."""
    level = 1
    while xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
    return level, xp


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    # Checked after each match with the updated totals and that match's result.
    unlocked: Callable[[PlayerStats, MatchPlayerResult], bool]


ACHIEVEMENTS = [
    Achievement("first_match", "Welcome to the arena", "Play your first match.", lambda t, m: t.matches_played >= 1),
    Achievement("first_win", "First blood bath", "Win a match.", lambda t, m: t.wins >= 1),
    Achievement("wins_10", "Contender", "Win 10 matches.", lambda t, m: t.wins >= 10),
    Achievement("kills_100", "Sharpshooter", "Get 100 kills.", lambda t, m: t.kills >= 100),
    Achievement("kills_1000", "Legend", "Get 1000 kills.", lambda t, m: t.kills >= 1000),
    Achievement("match_10_kills", "Rampage", "Get 10 kills in one match.", lambda t, m: m.kills >= 10),
    Achievement("flawless", "Untouchable", "Win a match without dying.", lambda t, m: m.placement == 1 and m.deaths == 0),
]
ACHIEVEMENTS_BY_ID = {a.id: a for a in ACHIEVEMENTS}
