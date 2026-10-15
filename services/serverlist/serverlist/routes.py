import json
import secrets
import time
import uuid

from fastapi import APIRouter, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from tpsarena_common.events import MatchFinished, PlayerMatchChanged, publish

from serverlist import scripts
from serverlist.config import get_settings
from serverlist.deps import Credentials, CurrentUserId, GameServer, SocialService, check_server_token, hash_server_token
from serverlist.errors import APIError
from serverlist.redis_client import (
    ACTIVE_SERVERS,
    EVENTS_STREAM,
    redis,
    server_key,
    server_party_key,
    server_tickets_key,
    ticket_key,
)
from serverlist.schemas import (
    AllocationRequest,
    AllocationResponse,
    ErrorResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    JoinTicketResponse,
    MatchFinishedRequest,
    PlayerMatchRequest,
    PublicServer,
    RegisterServerRequest,
    RegisterServerResponse,
    ServerListResponse,
    ServerStatus,
    VerifyTicketRequest,
    VerifyTicketResponse,
)

router = APIRouter()

SERVER_ERRORS = {401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_ticket_id() -> str:
    return secrets.token_urlsafe(24)


async def sweep() -> int:
    """Remove servers that missed their heartbeats (crashed) and publish server.removed for each."""
    settings = get_settings()
    stale_before = now_ms() - settings.server_ttl_seconds * 1000
    removed = 0
    for sid in await redis.zrangebyscore(ACTIVE_SERVERS, "-inf", stale_before):
        removed += await scripts.REMOVE(
            keys=[server_key(sid), server_tickets_key(sid), ACTIVE_SERVERS, EVENTS_STREAM, server_party_key(sid)],
            args=[sid, "timed_out", stale_before, str(uuid.uuid4()), settings.events_stream_maxlen],
        )
    return removed


async def require_server(server_id: str, credentials: HTTPAuthorizationCredentials | None) -> None:
    if not await check_server_token(server_id, credentials):
        raise APIError(404, "server_not_found", "Unknown server, register again.")


async def active_servers() -> list[dict[str, str]]:
    """Servers with a recent heartbeat, with `server_id` and `reserved_slots` added."""
    settings = get_settings()
    now = now_ms()
    stale_before = now - settings.server_ttl_seconds * 1000
    # Servers that stopped heartbeating are skipped here and removed by the sweeper.
    server_ids = await redis.zrangebyscore(ACTIVE_SERVERS, stale_before, "+inf")

    async with redis.pipeline(transaction=False) as pipe:
        for server_id in server_ids:
            pipe.hgetall(server_key(server_id))
            pipe.zcount(server_tickets_key(server_id), now, "+inf")
        results = await pipe.execute()

    servers = []
    for server_id, data, reserved_slots in zip(server_ids, results[::2], results[1::2]):
        if data:
            servers.append({**data, "server_id": server_id, "reserved_slots": reserved_slots})
    return servers


# --- Game servers (shared secret to register, then the server's own token) ---


@router.post("/servers", status_code=status.HTTP_201_CREATED, dependencies=[GameServer])
async def register_server(body: RegisterServerRequest) -> RegisterServerResponse:
    """Returns the server's own token: the other game server routes require it."""
    settings = get_settings()
    server_id = str(uuid.uuid4())
    server_token = secrets.token_urlsafe(32)
    key = server_key(server_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(
            key,
            mapping={
                **body.model_dump(),
                "status": ServerStatus.idle,
                "players": 0,
                "registered_at": now_ms(),
                "token_hash": hash_server_token(server_token),
            },
        )
        pipe.expire(key, get_settings().server_ttl_seconds)
        pipe.zadd(ACTIVE_SERVERS, {server_id: now_ms()})
        await pipe.execute()
    return RegisterServerResponse(
        server_id=server_id,
        server_token=server_token,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )


@router.put("/servers/{server_id}/heartbeat", responses=SERVER_ERRORS)
async def heartbeat(server_id: uuid.UUID, body: HeartbeatRequest, credentials: Credentials) -> HeartbeatResponse:
    """Every 5 s, and right away on important changes (match started, ended, player count)."""
    if body.status == ServerStatus.reserved:
        raise APIError(400, "invalid_status", "Only an allocation can reserve a server.")
    sid = str(server_id)
    await require_server(sid, credentials)
    settings = get_settings()
    result = await scripts.HEARTBEAT(
        keys=[server_key(sid), server_tickets_key(sid), ACTIVE_SERVERS, EVENTS_STREAM, server_party_key(sid)],
        args=[
            now_ms(),
            get_settings().server_ttl_seconds,
            body.status,
            body.players,
            body.map,
            sid,
            str(uuid.uuid4()),
            settings.events_stream_maxlen,
        ],
    )
    if result is None:
        # Missed heartbeats for too long and was dropped: the server must register again.
        raise APIError(404, "server_not_found", "Unknown server, register again.")
    server = dict(zip(result[::2], result[1::2]))
    return HeartbeatResponse(
        status=server["status"],
        party_id=server.get("party_id"),
        reserved_slots=int(server["reserved_slots"]),
        match_settings=json.loads(server["match_settings"]) if "match_settings" in server else None,
    )


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT, responses={401: {"model": ErrorResponse}})
async def deregister_server(server_id: uuid.UUID, credentials: Credentials) -> Response:
    """Shutting down. Idempotent: an unknown (already removed) server gives the same answer."""
    sid = str(server_id)
    if await check_server_token(sid, credentials):
        await scripts.REMOVE(
            keys=[server_key(sid), server_tickets_key(sid), ACTIVE_SERVERS, EVENTS_STREAM, server_party_key(sid)],
            args=[sid, "deregistered", "", str(uuid.uuid4()), get_settings().events_stream_maxlen],
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tickets/verify", responses=SERVER_ERRORS)
async def verify_ticket(body: VerifyTicketRequest, credentials: Credentials) -> VerifyTicketResponse:
    """Called by a game server when a player connects. A ticket works once, on its own server only."""
    sid = str(body.server_id)
    await require_server(sid, credentials)
    value = await scripts.VERIFY_TICKET(
        keys=[ticket_key(body.ticket), server_key(sid), server_tickets_key(sid)],
        args=[body.ticket, sid],
    )
    if value is None:
        raise APIError(401, "invalid_ticket", "The ticket is invalid, expired or already used.")
    return VerifyTicketResponse(user_id=json.loads(value)["user_id"])


@router.post(
    "/servers/{server_id}/match-finished",
    status_code=status.HTTP_202_ACCEPTED,
    responses={**SERVER_ERRORS, 403: {"model": ErrorResponse}},
)
async def match_finished(server_id: uuid.UUID, body: MatchFinishedRequest, credentials: Credentials) -> Response:
    """The server's match ended: published on match.finished (Stats saves it, Social puts the party
    back in the lobby). Game servers have no Redis access, so they send their events here."""
    await require_server(str(server_id), credentials)
    if body.server_id != server_id:
        raise APIError(403, "wrong_server", "A server can only report its own matches.")
    await publish(redis, MatchFinished(**body.model_dump()), maxlen=get_settings().events_stream_maxlen)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/servers/{server_id}/player-match", status_code=status.HTTP_202_ACCEPTED, responses=SERVER_ERRORS)
async def player_match(server_id: uuid.UUID, body: PlayerMatchRequest, credentials: Credentials) -> Response:
    """A player joined or left the server's match: published on player.match (Real-time shows
    them "in match" to their friends)."""
    await require_server(str(server_id), credentials)
    event = PlayerMatchChanged(user_id=body.user_id, server_id=server_id, action=body.action)
    await publish(redis, event, maxlen=get_settings().events_stream_maxlen)
    return Response(status_code=status.HTTP_202_ACCEPTED)


# --- Players (access token, through the BFF) ---


@router.get("/servers", responses={401: {"model": ErrorResponse}})
async def list_servers(
    _: CurrentUserId,
    build_version: str = Query(description="The client's build: only compatible servers are listed"),
    region: str | None = None,
) -> ServerListResponse:
    """Public list: matches in progress with a free slot. Idle and reserved servers never appear."""
    servers = [
        PublicServer(
            server_id=s["server_id"],
            name=s["name"],
            region=s["region"],
            map=s["map"],
            players=int(s["players"]),
            max_players=int(s["max_players"]),
        )
        for s in await active_servers()
        if s["status"] == ServerStatus.in_progress
        and s["build_version"] == build_version
        and (region is None or s["region"] == region)
        and int(s["players"]) + s["reserved_slots"] < int(s["max_players"])
    ]
    servers.sort(key=lambda s: (-s.players, s.name))
    return ServerListResponse(servers=servers)


@router.post(
    "/servers/{server_id}/join-ticket",
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_join_ticket(server_id: uuid.UUID, user_id: CurrentUserId) -> JoinTicketResponse:
    """A stranger joins a match in progress. The ticket holds a slot until it's used or expires."""
    settings = get_settings()
    sid = str(server_id)
    ticket = new_ticket_id()
    result = await scripts.JOIN_TICKET(
        keys=[server_key(sid), server_tickets_key(sid), ticket_key(ticket)],
        args=[
            now_ms(),
            settings.ticket_ttl_seconds * 1000,
            ticket,
            json.dumps({"user_id": str(user_id), "server_id": sid}),
        ],
    )
    if result == "not_found":
        raise APIError(404, "server_not_found", "This server doesn't exist any more.")
    if result == "not_joinable":
        raise APIError(409, "not_joinable", "This match can't be joined.")
    if result == "full":
        raise APIError(409, "server_full", "This match is full.")

    address, port = await redis.hmget(server_key(sid), "address", "port")
    return JoinTicketResponse(ticket=ticket, address=address, port=int(port), expires_in=settings.ticket_ttl_seconds)


# --- Social (service secret) ---


@router.post(
    "/allocations",
    status_code=status.HTTP_201_CREATED,
    dependencies=[SocialService],
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def allocate_server(body: AllocationRequest) -> AllocationResponse:
    """Reserve an idle server for a party launch, with one ticket (and slot) per member."""
    settings = get_settings()
    member_ids = list(dict.fromkeys(body.member_ids))  # dedupe, keep order
    if len(member_ids) > settings.max_players:
        raise APIError(400, "party_too_large", f"A party has at most {settings.max_players} members.")

    candidates = [
        s
        for s in await active_servers()
        if s["status"] == ServerStatus.idle
        and s["build_version"] == body.build_version
        and (body.region is None or s["region"] == body.region)
        and len(member_ids) <= int(s["max_players"])
    ]
    ttl_ms = settings.ticket_ttl_seconds * 1000
    for server in candidates:
        sid = server["server_id"]
        tickets = {user_id: new_ticket_id() for user_id in member_ids}
        now = now_ms()
        args: list = [
            now,
            ttl_ms,
            body.party_id,
            now + settings.reservation_seconds * 1000,
            body.build_version,
            json.dumps(body.match_settings),
        ]
        for user_id, ticket in tickets.items():
            args += [ticket, json.dumps({"user_id": str(user_id), "server_id": sid})]
        # Another allocation may take the same server first: then try the next one.
        reserved = await scripts.ALLOCATE(
            keys=[
                server_key(sid),
                server_tickets_key(sid),
                *(ticket_key(t) for t in tickets.values()),
                server_party_key(sid),
            ],
            args=args,
        )
        if reserved:
            return AllocationResponse(
                server_id=sid,
                address=server["address"],
                port=int(server["port"]),
                tickets=tickets,
                expires_in=settings.ticket_ttl_seconds,
            )
    raise APIError(503, "no_server_available", "No game server is available, try again shortly.")
