import uuid
from datetime import UTC, datetime

import httpx
from tpsarena_common.events import MatchFinished, PresenceChanged, UserDeleted

from social.db import SessionLocal
from social.models import Player
from social.worker import handle

from .conftest import new_player
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
