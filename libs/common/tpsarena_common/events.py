"""Event formats: the single source of truth for what goes through Redis Streams.

Every stream entry has three fields:
    event_id  unique id, consumers skip ids they already handled (delivery is at least once)
    type      e.g. "party.updated", picks the model below
    data      the model's other fields as JSON (nested values like member lists need JSON anyway)
"""

import json
import uuid
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis


class Event(BaseModel):
    STREAM: ClassVar[str]
    TYPE: ClassVar[str]

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)


EVENT_TYPES: dict[str, type[Event]] = {}


def event(stream: str, type_: str):
    """Class decorator: registers an event model under its type."""

    def register(cls: type[Event]) -> type[Event]:
        cls.STREAM = stream
        cls.TYPE = type_
        EVENT_TYPES[type_] = cls
        return cls

    return register


def encode(e: Event) -> dict[str, str]:
    return {"event_id": str(e.event_id), "type": e.TYPE, "data": e.model_dump_json(exclude={"event_id"})}


class UnknownEvent(Exception):
    pass


def decode(fields: dict[str, str]) -> Event:
    """Raises UnknownEvent for a type this code doesn't know, pydantic.ValidationError for a bad payload."""
    cls = EVENT_TYPES.get(fields.get("type", ""))
    if cls is None:
        raise UnknownEvent(fields.get("type"))
    return cls.model_validate({**json.loads(fields.get("data", "{}")), "event_id": fields["event_id"]})


async def publish(redis: Redis, e: Event, maxlen: int = 100_000) -> str:
    return await redis.xadd(e.STREAM, encode(e), maxlen=maxlen, approximate=True)


# --- Auth ---


@event("auth.emails", "auth.code_email")
class CodeEmail(Event):
    """Internal to Auth: the worker sends the email."""

    kind: Literal["verify", "reset"]
    email: str
    username: str
    code: str


@event("user.deleted", "user.deleted")
class UserDeleted(Event):
    user_id: uuid.UUID


# --- Real-time ---


@event("presence.changed", "presence.changed")
class PresenceChanged(Event):
    user_id: uuid.UUID
    status: Literal["online", "offline"]


# --- Game servers ---


@event("player.match", "player.match")
class PlayerMatchChanged(Event):
    """Published by the game server when a player joins or leaves its match (presence "in match")."""

    user_id: uuid.UUID
    server_id: uuid.UUID
    action: Literal["joined", "left"]


class MatchPlayerResult(BaseModel):
    user_id: uuid.UUID
    kills: int = Field(ge=0)
    deaths: int = Field(ge=0)
    placement: int = Field(ge=1)  # 1 = winner


@event("match.finished", "match.finished")
class MatchFinished(Event):
    """Published by the game server when a match ends. Consumed by Stats (results) and Social (party back to lobby).

    `players` holds everyone who played, including players who left before the end.
    """

    match_id: uuid.UUID
    server_id: uuid.UUID
    party_id: str | None = None  # the party that launched it, from the heartbeat reply
    map: str
    started_at: datetime
    ended_at: datetime
    players: list[MatchPlayerResult]

    @field_validator("players")
    @classmethod
    def one_result_per_player(cls, players: list[MatchPlayerResult]) -> list[MatchPlayerResult]:
        if len({p.user_id for p in players}) != len(players):
            raise ValueError("a player can only appear once")
        return players


# --- Social (all on social.events) ---


@event("social.events", "friend.request_sent")
class FriendRequestSent(Event):
    request_id: uuid.UUID
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID


@event("social.events", "friend.request_removed")
class FriendRequestRemoved(Event):
    """Declined by the recipient or cancelled by the sender."""

    request_id: uuid.UUID
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID


@event("social.events", "friend.added")
class FriendAdded(Event):
    user_id: uuid.UUID
    friend_id: uuid.UUID


@event("social.events", "friend.removed")
class FriendRemoved(Event):
    user_id: uuid.UUID
    friend_id: uuid.UUID


@event("social.events", "party.invite_sent")
class PartyInviteSent(Event):
    invite_id: str
    party_id: str
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    expires_at: datetime


@event("social.events", "party.updated")
class PartyUpdated(Event):
    """Full snapshot of the party. Keep the highest `version`: snapshots can arrive out of order."""

    party_id: str
    version: int
    leader_id: uuid.UUID
    status: Literal["lobby", "launching", "in_match"]
    member_ids: list[uuid.UUID]


@event("social.events", "party.match_ready")
class PartyMatchReady(Event):
    """Each member gets the address and only their own ticket."""

    party_id: str
    server_id: uuid.UUID
    address: str
    port: int
    tickets: dict[uuid.UUID, str]


@event("social.events", "party.disbanded")
class PartyDisbanded(Event):
    party_id: str


# --- Stats (all on stats.progress) ---


@event("stats.progress", "progress.level_up")
class LevelUp(Event):
    user_id: uuid.UUID
    level: int
    match_id: uuid.UUID


@event("stats.progress", "progress.achievement_unlocked")
class AchievementUnlocked(Event):
    user_id: uuid.UUID
    achievement_id: str
    match_id: uuid.UUID
