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


class MatchSettings(BaseModel):
    """The leader's choices for the match, applied by the game server. Map and mode will come later."""

    bots: int = Field(default=2, ge=0, le=7, description="Bots added to the match (only into free slots)")
    duration_seconds: int = Field(default=180, ge=60, le=600)


class LaunchRequest(BaseModel):
    build_version: str = Field(min_length=1, max_length=64)
    region: str | None = None
    match_settings: MatchSettings = Field(default_factory=MatchSettings)


class LaunchResponse(BaseModel):
    """The leader's own ticket; every member gets theirs through party.match_ready."""

    server_id: uuid.UUID
    address: str
    port: int
    ticket: str
