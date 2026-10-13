import uuid
from datetime import UTC, datetime

import httpx
import pytest
from redis.asyncio import Redis
from tpsarena_common.events import MatchFinished, PresenceChanged, ReservationExpired, ServerRemoved, UserDeleted

from social.db import SessionLocal
from social.events import outbox
from social.models import Player
from social.worker import handle

from .conftest import events, new_player
from .test_friends import befriend, friends_of
from .test_parties import allocation_for, create_party, invite_and_join, party


def match_finished(**fields) -> MatchFinished:
    now = datetime.now(UTC)
    return MatchFinished(match_id=uuid.uuid4(), map="arena", started_at=now, ended_at=now, players=[], **fields)


async def test_user_deleted(client):
    alice, bob = new_player(), new_player()
    await befriend(client, alice, bob)
    await client.patch("/profiles/me", json={"bio": "hi"}, headers=alice.headers)
    party_id = await create_party(client, bob)
    await invite_and_join(client, party_id, bob, alice)

    await handle(UserDeleted(user_id=alice.user_id))

    assert (await friends_of(client, bob))["friends"] == []
    assert (await party(client, party_id, bob)).json()["member_ids"] == [str(bob.user_id)]
    async with SessionLocal() as session:
        assert await session.get(Player, alice.user_id) is None


async def test_offline_player_leaves_party(client):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)
    await invite_and_join(client, party_id, leader, friend)

    await handle(PresenceChanged(user_id=friend.user_id, status="online"))
    assert len((await party(client, party_id, leader)).json()["member_ids"]) == 2

    await handle(PresenceChanged(user_id=friend.user_id, status="offline"))
    assert (await party(client, party_id, leader)).json()["member_ids"] == [str(leader.user_id)]


async def test_match_finished_returns_party_to_lobby(client, mocked):
    leader = new_player()
    party_id = await create_party(client, leader)
    allocation = allocation_for([leader])
    mocked.post("http://serverlist.test/allocations").mock(return_value=httpx.Response(201, json=allocation))
    await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)

    # From another server (e.g. an older match): ignored.
    await handle(match_finished(server_id=uuid.uuid4(), party_id=party_id))
    assert (await party(client, party_id, leader)).json()["status"] == "in_match"

    await handle(match_finished(server_id=allocation["server_id"], party_id=party_id))
    assert (await party(client, party_id, leader)).json()["status"] == "lobby"


async def test_match_finished_without_party_is_ignored():
    await handle(match_finished(server_id=uuid.uuid4()))


async def launched_party(client, mocked) -> tuple:
    leader = new_player()
    party_id = await create_party(client, leader)
    allocation = allocation_for([leader])
    mocked.post("http://serverlist.test/allocations").mock(return_value=httpx.Response(201, json=allocation))
    await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert (await party(client, party_id, leader)).json()["status"] == "in_match"
    return leader, party_id, allocation["server_id"]


@pytest.mark.parametrize("reason", ["timed_out", "deregistered"])
async def test_lost_server_returns_party_to_lobby(client, mocked, reason):
    leader, party_id, server_id = await launched_party(client, mocked)

    await handle(ServerRemoved(server_id=uuid.uuid4(), party_id=party_id, reason=reason))  # another server
    assert (await party(client, party_id, leader)).json()["status"] == "in_match"

    await handle(ServerRemoved(server_id=server_id, party_id=party_id, reason=reason))
    assert (await party(client, party_id, leader)).json()["status"] == "lobby"
    assert (await events("party.updated"))[-1].status == "lobby"


async def test_expired_reservation_returns_party_to_lobby(client, mocked):
    leader, party_id, server_id = await launched_party(client, mocked)
    await handle(ReservationExpired(server_id=server_id, party_id=party_id))
    assert (await party(client, party_id, leader)).json()["status"] == "lobby"


async def test_removed_server_without_party_is_ignored():
    await handle(ServerRemoved(server_id=uuid.uuid4(), reason="timed_out"))


async def test_friend_events_wait_in_the_outbox_while_redis_is_down(client, monkeypatch):
    alice, bob = new_player(), new_player()
    down = Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True)
    monkeypatch.setattr(outbox, "redis", down)
    r = await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)
    assert r.status_code == 201  # saved; only the notification waits
    assert await events("friend.request_sent") == []

    monkeypatch.undo()
    await outbox.flush()  # what the worker does every few seconds
    assert len(await events("friend.request_sent")) == 1
    await down.aclose()
