import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, MetaData, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(schema="stats")


class Match(Base):
    __tablename__ = "matches"

    match_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    server_id: Mapped[uuid.UUID]
    party_id: Mapped[str | None] = mapped_column(String(64))
    map: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MatchPlayer(Base):
    """One player's result in one match. `user_id` becomes null when the account is deleted."""

    __tablename__ = "match_players"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_match_players_match_user"),
        Index("ix_match_players_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None]
    kills: Mapped[int]
    deaths: Mapped[int]
    placement: Mapped[int]
    xp_gained: Mapped[int]


class PlayerStats(Base):
    """Running totals, updated with each match. Created lazily on a player's first match."""

    __tablename__ = "player_stats"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    matches_played: Mapped[int] = mapped_column(default=0)
    wins: Mapped[int] = mapped_column(default=0)
    kills: Mapped[int] = mapped_column(default=0)
    deaths: Mapped[int] = mapped_column(default=0)
    xp: Mapped[int] = mapped_column(default=0)
    level: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlayerAchievement(Base):
    __tablename__ = "player_achievements"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("player_stats.user_id", ondelete="CASCADE"), primary_key=True
    )
    achievement_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    match_id: Mapped[uuid.UUID]
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
