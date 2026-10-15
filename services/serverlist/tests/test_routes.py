import uuid

import httpx
from tpsarena_common.events import (
    Event,
    MatchFinished,
    PlayerMatchChanged,
    ReservationExpired,
    ServerRemoved,
    decode,
)

from serverlist.config import get_settings
from serverlist.redis_client import ACTIVE_SERVERS, EVENTS_STREAM, redis, server_key, server_party_key, server_tickets_key
from serverlist.routes import sweep

from .conftest import GAME_SERVER, SOCIAL, player

BUILD = "0.1.0"
TOKENS: dict[str, str] = {}  # server id -> its token, from registration


def as_server(server_id: str) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKENS[server_id]}"}


async def server_events() -> list[Event]:
    return [decode(fields) for _, fields in await redis.xrange(EVENTS_STREAM)]


async def register(client: httpx.AsyncClient, **overrides) -> str:
    body = {
        "name": "EU #1",
        "address": "203.0.113.10",
        "port": 7000,
        "region": "eu",
        "map": "arena",
        "build_version": BUILD,
        **overrides,
    }
    r = await client.post("/servers", json=body, headers=GAME_SERVER)
    assert r.status_code == 201, r.text
    TOKENS[r.json()["server_id"]] = r.json()["server_token"]
    return r.json()["server_id"]


async def heartbeat(client: httpx.AsyncClient, server_id: str, status: str, players: int = 0) -> httpx.Response:
    body = {"status": status, "players": players, "map": "arena"}
    return await client.put(f"/servers/{server_id}/heartbeat", json=body, headers=as_server(server_id))


async def allocate(client: httpx.AsyncClient, members: list[uuid.UUID], **overrides) -> httpx.Response:
    body = {"party_id": str(uuid.uuid4()), "member_ids": [str(m) for m in members], "build_version": BUILD}
    return await client.post("/allocations", json={**body, **overrides}, headers=SOCIAL)


async def list_servers(client: httpx.AsyncClient, build: str = BUILD) -> list[dict]:
    r = await client.get("/servers", params={"build_version": build}, headers=player())
    assert r.status_code == 200
    return r.json()["servers"]


async def verify(client: httpx.AsyncClient, ticket: str, server_id: str) -> httpx.Response:
    return await client.post("/tickets/verify", json={"ticket": ticket, "server_id": server_id}, headers=as_server(server_id))


# --- Authentication ---


async def test_game_server_routes_need_the_secret(client):
    body = {"name": "x", "address": "a", "port": 1, "region": "eu", "map": "m", "build_version": BUILD}
    assert (await client.post("/servers", json=body)).status_code == 401
    r = await client.post("/servers", json=body, headers={"authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_secret"
    # The social secret isn't a game server secret.
    assert (await client.post("/servers", json=body, headers=SOCIAL)).status_code == 401


async def test_allocations_need_the_social_secret(client):
    r = await client.post("/allocations", json={}, headers=GAME_SERVER)
    assert r.status_code == 401


async def test_player_routes_need_a_token(client):
    r = await client.get("/servers", params={"build_version": BUILD})
    assert r.status_code == 401
    assert r.json()["error"] == "not_authenticated"


# --- Registry ---


async def test_register_heartbeat_deregister(client):
    server_id = await register(client)
    r = await heartbeat(client, server_id, "idle", players=0)
    assert r.status_code == 200
    assert r.json() == {"status": "idle", "party_id": None, "leader_id": None, "reserved_slots": 0, "match_settings": None}

    r = await client.delete(f"/servers/{server_id}", headers=as_server(server_id))
    assert r.status_code == 204
    assert (await heartbeat(client, server_id, "idle")).status_code == 404
    [event] = await server_events()
    assert isinstance(event, ServerRemoved)
    assert (str(event.server_id), event.party_id, event.reason) == (server_id, None, "deregistered")
    # Idempotent.
    assert (await client.delete(f"/servers/{server_id}", headers=as_server(server_id))).status_code == 204
    assert len(await server_events()) == 1


async def test_each_server_has_its_own_token(client):
    server_id = await register(client)
    other_id = await register(client)
    body = {"status": "idle", "players": 0, "map": "arena"}
    r = await client.put(f"/servers/{server_id}/heartbeat", json=body, headers=as_server(other_id))
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_server_token"
    # The shared secret only registers.
    assert (await client.put(f"/servers/{server_id}/heartbeat", json=body, headers=GAME_SERVER)).status_code == 401
    assert (await client.put(f"/servers/{server_id}/heartbeat", json=body)).status_code == 401
    assert (await client.delete(f"/servers/{server_id}", headers=as_server(other_id))).status_code == 401
    r = await client.post("/tickets/verify", json={"ticket": "t", "server_id": server_id}, headers=as_server(other_id))
    assert r.status_code == 401
    assert (await heartbeat(client, server_id, "idle")).status_code == 200


async def test_register_rejects_more_than_16_players(client):
    body = {"name": "x", "address": "a", "port": 1, "region": "eu", "map": "m", "build_version": BUILD, "max_players": 17}
    assert (await client.post("/servers", json=body, headers=GAME_SERVER)).status_code == 422


async def test_server_cannot_reserve_itself(client):
    server_id = await register(client)
    r = await heartbeat(client, server_id, "reserved")
    assert r.status_code == 400


async def test_expired_server_must_register_again(client):
    server_id = await register(client)
    await redis.delete(server_key(server_id))  # what the 15 s TTL does
    r = await heartbeat(client, server_id, "idle")
    assert r.status_code == 404
    assert r.json()["error"] == "server_not_found"


# --- Public list ---


async def test_list_shows_only_joinable_matches(client):
    idle = await register(client, name="idle")
    full = await register(client, name="full")
    open_ = await register(client, name="open")
    other_build = await register(client, name="other build", build_version="0.0.9")
    await heartbeat(client, idle, "idle")
    await heartbeat(client, full, "in_progress", players=16)
    await heartbeat(client, open_, "in_progress", players=3)
    await heartbeat(client, other_build, "in_progress", players=1)

    servers = await list_servers(client)
    assert [s["name"] for s in servers] == ["open"]
    assert "address" not in servers[0]  # only given with a ticket
    assert [s["name"] for s in await list_servers(client, "0.0.9")] == ["other build"]


async def test_list_hides_stale_servers(client):
    server_id = await register(client)
    await heartbeat(client, server_id, "in_progress", players=1)
    await redis.zadd(ACTIVE_SERVERS, {server_id: 0})  # last heartbeat long ago
    assert await list_servers(client) == []


async def test_sweeper_removes_crashed_servers_and_names_their_party(client):
    crashed = await register(client)
    party_id = str(uuid.uuid4())
    await allocate(client, [uuid.uuid4()], party_id=party_id)
    alive = await register(client)
    await redis.zadd(ACTIVE_SERVERS, {crashed: 0})  # stopped heartbeating

    assert await sweep() == 1
    [event] = await server_events()
    assert isinstance(event, ServerRemoved)
    assert (str(event.server_id), event.party_id, event.reason) == (crashed, party_id, "timed_out")
    assert await redis.exists(server_key(crashed)) == 0
    assert (await heartbeat(client, crashed, "idle")).status_code == 404  # must register again
    assert await sweep() == 0  # once only
    assert await redis.zrange(ACTIVE_SERVERS, 0, -1) == [alive]


async def test_late_sweep_still_names_the_party(client):
    """Server List was down longer than the registry TTL: the hash is gone, the party key isn't."""
    server_id = await register(client)
    party_id = str(uuid.uuid4())
    await allocate(client, [uuid.uuid4()], party_id=party_id)
    await redis.zadd(ACTIVE_SERVERS, {server_id: 0})
    await redis.delete(server_key(server_id))  # what the 15 s TTL does

    assert await sweep() == 1
    [event] = await server_events()
    assert (str(event.server_id), event.party_id) == (server_id, party_id)
    assert await redis.exists(server_party_key(server_id)) == 0


async def test_party_key_goes_when_the_server_is_idle_again(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "reservation_seconds", 0)
    server_id = await register(client)
    await allocate(client, [uuid.uuid4()])
    assert await redis.exists(server_party_key(server_id)) == 1
    await heartbeat(client, server_id, "idle")
    assert await redis.exists(server_party_key(server_id)) == 0


async def test_sweeper_spares_a_server_that_just_sent_a_heartbeat(client):
    server_id = await register(client)
    await heartbeat(client, server_id, "idle")
    assert await sweep() == 0
    assert await server_events() == []


# --- Party allocation ---


async def test_allocation_reserves_server_with_one_ticket_per_member(client):
    server_id = await register(client)
    members = [uuid.uuid4() for _ in range(3)]
    r = await allocate(client, members)
    assert r.status_code == 201
    allocation = r.json()
    assert allocation["server_id"] == server_id
    assert (allocation["address"], allocation["port"]) == ("203.0.113.10", 7000)
    assert set(allocation["tickets"]) == {str(m) for m in members}

    # The game server learns about the party from its next heartbeat, even if it still says idle.
    r = await heartbeat(client, server_id, "idle")
    assert r.json()["status"] == "reserved"
    assert r.json()["reserved_slots"] == 3
    assert r.json()["party_id"]
    assert r.json()["match_settings"] == {}
    assert r.json()["leader_id"] is None

    # Reserved servers aren't in the public list and can't be allocated twice.
    await heartbeat(client, server_id, "idle")
    assert await list_servers(client) == []
    assert (await allocate(client, [uuid.uuid4()])).status_code == 503


async def test_allocation_hands_match_settings_to_the_server(client):
    server_id = await register(client)
    match_settings = {"bots": 3, "duration_seconds": 300}
    await allocate(client, [uuid.uuid4()], match_settings=match_settings)
    assert (await heartbeat(client, server_id, "idle")).json()["match_settings"] == match_settings
    assert (await heartbeat(client, server_id, "in_progress", players=1)).json()["match_settings"] == match_settings
    # Match over: the next party brings its own settings.
    assert (await heartbeat(client, server_id, "idle")).json()["match_settings"] is None


async def test_allocation_hands_the_party_leader_to_the_server(client):
    server_id = await register(client)
    leader = uuid.uuid4()
    await allocate(client, [leader, uuid.uuid4()], leader_id=str(leader))
    assert (await heartbeat(client, server_id, "idle")).json()["leader_id"] == str(leader)
    assert (await heartbeat(client, server_id, "in_progress", players=2)).json()["leader_id"] == str(leader)
    assert (await heartbeat(client, server_id, "idle")).json()["leader_id"] is None


async def test_allocation_without_idle_server(client):
    r = await allocate(client, [uuid.uuid4()])
    assert r.status_code == 503
    assert r.json()["error"] == "no_server_available"


async def test_allocation_matches_build_and_region(client):
    await register(client, build_version="0.0.9")
    eu = await register(client, region="eu")
    us = await register(client, region="us")
    assert (await allocate(client, [uuid.uuid4()], region="us")).json()["server_id"] == us
    assert (await allocate(client, [uuid.uuid4()], region="eu")).json()["server_id"] == eu
    assert (await allocate(client, [uuid.uuid4()])).status_code == 503


async def test_allocation_rejects_party_over_16(client):
    await register(client)
    r = await allocate(client, [uuid.uuid4() for _ in range(17)])
    assert r.status_code == 400
    assert r.json()["error"] == "party_too_large"


async def test_reserved_server_returns_to_idle_after_reservation_window(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "reservation_seconds", 0)
    server_id = await register(client)
    party_id = str(uuid.uuid4())
    await allocate(client, [uuid.uuid4()], party_id=party_id)
    r = await heartbeat(client, server_id, "idle")
    assert r.json() == {"status": "idle", "party_id": None, "leader_id": None, "reserved_slots": 0, "match_settings": None}
    # The party won't play there: Social puts it back in the lobby.
    [event] = await server_events()
    assert isinstance(event, ReservationExpired)
    assert (str(event.server_id), event.party_id) == (server_id, party_id)
    await heartbeat(client, server_id, "idle")
    assert len(await server_events()) == 1


async def test_reservation_holds_while_players_wait_in_the_lobby(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "reservation_seconds", 0)
    server_id = await register(client)
    await allocate(client, [uuid.uuid4()])
    # The leader hasn't started yet: the window is over but a member is connected.
    assert (await heartbeat(client, server_id, "idle", players=1)).json()["status"] == "reserved"
    assert await server_events() == []
    # Everybody left the lobby: the reservation ends.
    assert (await heartbeat(client, server_id, "idle", players=0)).json()["status"] == "idle"
    [event] = await server_events()
    assert isinstance(event, ReservationExpired)


async def test_idle_heartbeat_during_reservation_publishes_nothing(client):
    server_id = await register(client)
    await allocate(client, [uuid.uuid4()])
    assert (await heartbeat(client, server_id, "idle")).json()["status"] == "reserved"
    assert await server_events() == []


async def test_party_flow_into_public_list(client):
    server_id = await register(client)
    members = [uuid.uuid4() for _ in range(2)]
    tickets = (await allocate(client, members)).json()["tickets"]

    for user_id, ticket in tickets.items():
        r = await verify(client, ticket, server_id)
        assert r.status_code == 200
        assert r.json()["user_id"] == user_id

    r = await heartbeat(client, server_id, "in_progress", players=2)
    assert r.json()["status"] == "in_progress"
    assert r.json()["reserved_slots"] == 0
    assert [s["server_id"] for s in await list_servers(client)] == [server_id]

    # Match over: back to idle, ready for the next party.
    r = await heartbeat(client, server_id, "idle", players=0)
    assert r.json() == {"status": "idle", "party_id": None, "leader_id": None, "reserved_slots": 0, "match_settings": None}


# --- Join tickets and verification ---


async def test_stranger_joins_match_in_progress(client):
    server_id = await register(client)
    await heartbeat(client, server_id, "in_progress", players=3)
    user_id = uuid.uuid4()

    r = await client.post(f"/servers/{server_id}/join-ticket", headers=player(user_id))
    assert r.status_code == 201
    joined = r.json()
    assert (joined["address"], joined["port"], joined["expires_in"]) == ("203.0.113.10", 7000, 30)

    r = await verify(client, joined["ticket"], server_id)
    assert r.json()["user_id"] == str(user_id)
    # Single use.
    assert (await verify(client, joined["ticket"], server_id)).status_code == 401


async def test_ticket_only_works_on_its_server(client):
    server_id = await register(client)
    other_id = await register(client)
    await heartbeat(client, server_id, "in_progress", players=1)
    ticket = (await client.post(f"/servers/{server_id}/join-ticket", headers=player())).json()["ticket"]

    r = await verify(client, ticket, other_id)
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_ticket"
    # Shown to the wrong server, it's burnt.
    assert (await verify(client, ticket, server_id)).status_code == 401


async def test_unused_tickets_hold_slots(client):
    server_id = await register(client)
    await heartbeat(client, server_id, "in_progress", players=14)

    for _ in range(2):
        r = await client.post(f"/servers/{server_id}/join-ticket", headers=player())
        assert r.status_code == 201
    # 14 players + 2 pending tickets = 16: full, and gone from the list.
    r = await client.post(f"/servers/{server_id}/join-ticket", headers=player())
    assert r.status_code == 409
    assert r.json()["error"] == "server_full"
    assert await list_servers(client) == []


async def test_expired_tickets_free_their_slot(client):
    server_id = await register(client)
    await heartbeat(client, server_id, "in_progress", players=15)
    ticket = (await client.post(f"/servers/{server_id}/join-ticket", headers=player())).json()["ticket"]
    assert (await client.post(f"/servers/{server_id}/join-ticket", headers=player())).status_code == 409

    await redis.zadd(server_tickets_key(server_id), {ticket: 0})  # what 30 s without using it does
    r = await client.post(f"/servers/{server_id}/join-ticket", headers=player())
    assert r.status_code == 201


async def test_cannot_join_idle_or_reserved_server(client):
    server_id = await register(client)
    r = await client.post(f"/servers/{server_id}/join-ticket", headers=player())
    assert r.status_code == 409
    assert r.json()["error"] == "not_joinable"

    r = await client.post(f"/servers/{uuid.uuid4()}/join-ticket", headers=player())
    assert r.status_code == 404


# --- Game server events (published for the game server, which has no Redis access) ---


async def stream_events(stream: str) -> list[Event]:
    return [decode(fields) for _, fields in await redis.xrange(stream)]


def match_result(server_id: str, **overrides) -> dict:
    return {
        "match_id": str(uuid.uuid4()),
        "server_id": server_id,
        "party_id": "p1",
        "map": "neo-edo",
        "started_at": "2026-10-14T10:00:00Z",
        "ended_at": "2026-10-14T10:03:00Z",
        "players": [
            {"user_id": str(uuid.uuid4()), "kills": 5, "deaths": 1, "placement": 1},
            {"user_id": str(uuid.uuid4()), "kills": 1, "deaths": 5, "placement": 2},
        ],
        **overrides,
    }


async def test_match_finished_is_published(client):
    server_id = await register(client)
    body = match_result(server_id)
    r = await client.post(f"/servers/{server_id}/match-finished", json=body, headers=as_server(server_id))
    assert r.status_code == 202, r.text
    [event] = await stream_events("match.finished")
    assert isinstance(event, MatchFinished)
    assert str(event.match_id) == body["match_id"]
    assert str(event.server_id) == server_id
    assert event.party_id == "p1"
    assert [p.kills for p in event.players] == [5, 1]


async def test_match_finished_only_for_the_calling_server(client):
    server_id = await register(client)
    other = await register(client)
    url = f"/servers/{server_id}/match-finished"
    # Its own token, but another server's match.
    r = await client.post(url, json=match_result(other), headers=as_server(server_id))
    assert r.status_code == 403
    assert r.json()["error"] == "wrong_server"
    # Another server's token.
    r = await client.post(url, json=match_result(server_id), headers=as_server(other))
    assert r.status_code == 401
    # An unknown (or removed) server registers again.
    unknown = str(uuid.uuid4())
    r = await client.post(f"/servers/{unknown}/match-finished", json=match_result(unknown), headers=as_server(server_id))
    assert r.status_code == 404
    assert await stream_events("match.finished") == []


async def test_match_finished_validates_the_result(client):
    server_id = await register(client)
    twice = str(uuid.uuid4())
    duplicate = [{"user_id": twice, "kills": 0, "deaths": 0, "placement": 1}] * 2
    for body in (match_result(server_id, players=duplicate), match_result(server_id, map=""), {"server_id": server_id}):
        r = await client.post(f"/servers/{server_id}/match-finished", json=body, headers=as_server(server_id))
        assert r.status_code == 422
    assert await stream_events("match.finished") == []


async def test_player_match_is_published(client):
    server_id = await register(client)
    user_id = str(uuid.uuid4())
    for action in ("joined", "left"):
        body = {"user_id": user_id, "action": action}
        r = await client.post(f"/servers/{server_id}/player-match", json=body, headers=as_server(server_id))
        assert r.status_code == 202, r.text
    events = await stream_events("player.match")
    assert all(isinstance(e, PlayerMatchChanged) for e in events)
    assert [(str(e.user_id), str(e.server_id), e.action) for e in events] == [
        (user_id, server_id, "joined"),
        (user_id, server_id, "left"),
    ]


async def test_player_match_needs_the_server_token(client):
    server_id = await register(client)
    body = {"user_id": str(uuid.uuid4()), "action": "joined"}
    assert (await client.post(f"/servers/{server_id}/player-match", json=body)).status_code == 401
    r = await client.post(f"/servers/{server_id}/player-match", json=body, headers=GAME_SERVER)
    assert r.status_code == 401  # the shared secret is only for registering
    invalid = {**body, "action": "spectating"}
    r = await client.post(f"/servers/{server_id}/player-match", json=invalid, headers=as_server(server_id))
    assert r.status_code == 422
    assert await stream_events("player.match") == []
