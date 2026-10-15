import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from social.config import get_settings


class ErrorResponse(BaseModel):
    error: str
    message: str


# --- Profiles ---


class ProfileResponse(BaseModel):
    user_id: uuid.UUID
    bio: str
    avatar: str


class ProfileUpdate(BaseModel):
    bio: str | None = None
    avatar: str | None = None

    @field_validator("bio")
    @classmethod
    def bio_length(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) > get_settings().bio_max_length:
                raise ValueError(f"bio can't be longer than {get_settings().bio_max_length} characters")
        return v

    @field_validator("avatar")
    @classmethod
    def known_avatar(cls, v: str | None) -> str | None:
        if v is not None and v not in get_settings().avatars:
            raise ValueError(f"avatar must be one of {get_settings().avatars}")
        return v


# --- Loadouts (saved classes) ---


class LoadoutItem(BaseModel):
    slot: int = Field(ge=0, le=9)
    name: str
    primary_weapon: str
    secondary_weapon: str

    @field_validator("name")
    @classmethod
    def name_length(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > get_settings().loadout_name_max_length:
            raise ValueError(f"name must be 1 to {get_settings().loadout_name_max_length} characters")
        return v

    @field_validator("primary_weapon")
    @classmethod
    def known_primary(cls, v: str) -> str:
        if v not in get_settings().primary_weapons:
            raise ValueError(f"primary_weapon must be one of {get_settings().primary_weapons}")
        return v

    @field_validator("secondary_weapon")
    @classmethod
    def known_secondary(cls, v: str) -> str:
        if v not in get_settings().secondary_weapons:
            raise ValueError(f"secondary_weapon must be one of {get_settings().secondary_weapons}")
        return v


class LoadoutsUpdate(BaseModel):
    """The whole list: it replaces every saved class of the player."""

    loadouts: list[LoadoutItem]

    @field_validator("loadouts")
    @classmethod
    def at_most_max_with_unique_slots(cls, v: list[LoadoutItem]) -> list[LoadoutItem]:
        if len(v) > get_settings().max_loadouts:
            raise ValueError(f"at most {get_settings().max_loadouts} loadouts")
        if len({item.slot for item in v}) != len(v):
            raise ValueError("slots must be unique")
        return v


class LoadoutsResponse(BaseModel):
    loadouts: list[LoadoutItem]


# --- Friends ---


class UserIdRequest(BaseModel):
    user_id: uuid.UUID


class FriendRequestResult(BaseModel):
    """`accepted` when the other player had already sent you a request."""

    status: Literal["pending", "accepted"]
    request_id: uuid.UUID | None


class Friend(BaseModel):
    user_id: uuid.UUID
    since: datetime


class PendingRequest(BaseModel):
    request_id: uuid.UUID
    user_id: uuid.UUID  # the other player
    created_at: datetime


class FriendsResponse(BaseModel):
    friends: list[Friend]
    incoming: list[PendingRequest]
    outgoing: list[PendingRequest]


# --- Parties ---


class PartyResponse(BaseModel):
    party_id: str
    leader_id: uuid.UUID
    status: Literal["lobby", "launching", "in_match"]
    member_ids: list[uuid.UUID]
    version: int


class InviteResponse(BaseModel):
    invite_id: str
    expires_at: datetime


class LaunchRequest(BaseModel):
    build_version: str = Field(min_length=1, max_length=64)
    region: str | None = None


class LaunchResponse(BaseModel):
    """The leader's own ticket; every member gets theirs through party.match_ready."""

    server_id: uuid.UUID
    address: str
    port: int
    ticket: str
