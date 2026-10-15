import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, MetaData, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from tpsarena_common.outbox import outbox_table


class Base(DeclarativeBase):
    metadata = MetaData(schema="social")


class Player(Base):
    """Created lazily the first time Social sees a user id (no foreign key to Auth's schema)."""

    __tablename__ = "players"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    bio: Mapped[str] = mapped_column(String(200), default="", server_default="")
    avatar: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FriendRequest(Base):
    __tablename__ = "friend_requests"
    __table_args__ = (
        UniqueConstraint("from_user_id", "to_user_id", name="uq_friend_requests_pair"),
        CheckConstraint("from_user_id <> to_user_id", name="ck_friend_requests_not_self"),
        Index("ix_friend_requests_to_user_id", "to_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    from_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"))
    to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Friendship(Base):
    """Stored both ways (A→B and B→A), so "friends of X" is one indexed lookup."""

    __tablename__ = "friendships"
    __table_args__ = (CheckConstraint("user_id <> friend_id", name="ck_friendships_not_self"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    friend_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Loadout(Base):
    """A player's saved class (game equipment): one row per slot, 0 to 9.

    Weapon ids are the game's `WeaponData.id` values; the allowed lists are in the settings.
    """

    __tablename__ = "loadouts"
    __table_args__ = (CheckConstraint("slot >= 0 AND slot < 10", name="ck_loadouts_slot"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(24))
    primary_weapon: Mapped[str] = mapped_column(String(24))
    secondary_weapon: Mapped[str] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Events waiting to be published (see tpsarena_common.outbox).
Outbox = outbox_table(Base.metadata)
