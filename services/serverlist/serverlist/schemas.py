import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from tpsarena_common.events import MatchPlayerResult

from serverlist.config import get_settings


class ServerStatus(StrEnum):
    idle = "idle"
    reserved = "reserved"
    in_progress = "in_progress"
    ending = "ending"


class RegisterServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    region: str = Field(min_length=1, max_length=32)
    map: str = Field(min_length=1, max_length=64)
    max_players: int = Field(default_factory=lambda: get_settings().max_players, ge=1)
    build_version: str = Field(min_length=1, max_length=64)

    @field_validator("max_players")
    @classmethod
    def at_most_party_size(cls, v: int) -> int:
        limit = get_settings().max_players
        if v > limit:
            raise ValueError(f"max_players can't be more than {limit}")
        return v


class RegisterServerResponse(BaseModel):
    server_id: uuid.UUID
    # Send it as "Authorization: Bearer <server_token>" on every other call; keep it secret.
    server_token: str
    heartbeat_interval_seconds: int


class HeartbeatRequest(BaseModel):
    # A server can't put itself in "reserved": only an allocation does that.
    status: ServerStatus = Field(description="idle, in_progress or ending")
    players: int = Field(ge=0)
    map: str = Field(min_length=1, max_length=64)


class HeartbeatResponse(BaseModel):
    """What the game server should know: e.g. that it was reserved for a party."""

    status: ServerStatus
    party_id: str | None
    reserved_slots: int


class PublicServer(BaseModel):
    server_id: uuid.UUID
    name: str
    region: str
    map: str
    players: int
    max_players: int


class ServerListResponse(BaseModel):
    servers: list[PublicServer]


class JoinTicketResponse(BaseModel):
    ticket: str
    address: str
    port: int
    expires_in: int


class VerifyTicketRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=64)
    server_id: uuid.UUID


class VerifyTicketResponse(BaseModel):
    user_id: uuid.UUID


class AllocationRequest(BaseModel):
    party_id: str = Field(min_length=1, max_length=64)
    member_ids: list[uuid.UUID] = Field(min_length=1)
    build_version: str = Field(min_length=1, max_length=64)
    region: str | None = None


class AllocationResponse(BaseModel):
    server_id: uuid.UUID
    address: str
    port: int
    tickets: dict[uuid.UUID, str]  # user_id -> ticket
    expires_in: int


class MatchFinishedRequest(BaseModel):
    """The fields of the match.finished event (tpsarena_common.events.MatchFinished) without
    event_id, which the Server List adds when it publishes."""

    match_id: uuid.UUID
    server_id: uuid.UUID = Field(description="Must be the calling server's id")
    party_id: str | None = Field(default=None, max_length=64)
    map: str = Field(min_length=1, max_length=64)
    started_at: datetime
    ended_at: datetime
    players: list[MatchPlayerResult] = Field(description="Everyone who played, including players who left")

    @field_validator("players")
    @classmethod
    def one_result_per_player(cls, players: list[MatchPlayerResult]) -> list[MatchPlayerResult]:
        if len({p.user_id for p in players}) != len(players):
            raise ValueError("a player can only appear once")
        return players


class PlayerMatchRequest(BaseModel):
    """A player entered or left the server's match (player.match event, server_id added)."""

    user_id: uuid.UUID
    action: Literal["joined", "left"]


class ErrorResponse(BaseModel):
    error: str
    message: str
