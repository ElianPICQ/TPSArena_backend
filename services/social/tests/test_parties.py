import json
import uuid

import httpx

from social import clients, parties

from .conftest import Player, events, new_player
from .test_friends import befriend


async def create_party(client: httpx.AsyncClient, p: Player) -> str:
    r = await client.post("/parties", headers=p.headers)
    assert r.status_code == 201, r.text
    return r.json()["party_id"]


async def invite_and_join(client: httpx.AsyncClient, party_id: str, host: Player, guest: Player) -> dict:
    r = await client.post(f"/parties/{party_id}/invite", json={"user_id": str(guest.user_id)}, headers=host.headers)
    assert r.status_code == 201, r.text
    r = await client.post(f"/invites/{r.json()['invite_id']}/accept", headers=guest.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def party(client: httpx.AsyncClient, party_id: str, p: Player) -> httpx.Response:
    return await client.get(f"/parties/{party_id}", headers=p.headers)


async def test_create_and_get(client):
    leader, stranger = new_player(), new_player()
    party_id = await create_party(client, leader)
    r = await party(client, party_id, leader)
    assert r.json()["leader_id"] == str(leader.user_id)
    assert r.json()["status"] == "lobby"
    assert r.json()["member_ids"] == [str(leader.user_id)]
    # Only members can see it.
    assert (await party(client, party_id, stranger)).status_code == 404
    # One party per player.
    r = await client.post("/parties", headers=leader.headers)
    assert (r.status_code, r.json()["error"]) == (409, "already_in_party")


async def test_invite_friend_and_join(client):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)

    joined = await invite_and_join(client, party_id, leader, friend)
    assert set(joined["member_ids"]) == {str(leader.user_id), str(friend.user_id)}

    invites = await events("party.invite_sent")
    assert invites[0].to_user_id == friend.user_id
    updates = await events("party.updated")
    assert [u.version for u in updates] == [1, 2]
    assert len(updates[-1].member_ids) == 2


async def test_can_only_invite_friends(client):
    leader, stranger = new_player(), new_player()
    party_id = await create_party(client, leader)
    r = await client.post(f"/parties/{party_id}/invite", json={"user_id": str(stranger.user_id)}, headers=leader.headers)
    assert (r.status_code, r.json()["error"]) == (403, "not_friends")


async def test_invite_is_single_use_and_personal(client):
    leader, friend, other = new_player(), new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)
    invite_id = (
        await client.post(f"/parties/{party_id}/invite", json={"user_id": str(friend.user_id)}, headers=leader.headers)
    ).json()["invite_id"]

    assert (await client.post(f"/invites/{invite_id}/accept", headers=other.headers)).status_code == 404
    assert (await client.post(f"/invites/{invite_id}/accept", headers=friend.headers)).status_code == 200
    await client.post(f"/parties/{party_id}/leave", headers=friend.headers)
    assert (await client.post(f"/invites/{invite_id}/accept", headers=friend.headers)).status_code == 404


async def test_accepting_an_invite_leaves_your_party(client):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    own_party = await create_party(client, friend)
    party_id = await create_party(client, leader)

    await invite_and_join(client, party_id, leader, friend)
    assert (await party(client, own_party, friend)).status_code == 404
    assert [e.party_id for e in await events("party.disbanded")] == [own_party]


async def test_party_holds_at_most_8(client):
    leader = new_player()
    party_id = await create_party(client, leader)
    for _ in range(7):
        friend = new_player()
        await befriend(client, leader, friend)
        await invite_and_join(client, party_id, leader, friend)

    ninth = new_player()
    await befriend(client, leader, ninth)
    r = await client.post(f"/parties/{party_id}/invite", json={"user_id": str(ninth.user_id)}, headers=leader.headers)
    assert (r.status_code, r.json()["error"]) == (409, "party_full")


async def test_leader_leaves_then_last_member_disbands(client):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)
    await invite_and_join(client, party_id, leader, friend)

    assert (await client.post(f"/parties/{party_id}/leave", headers=leader.headers)).status_code == 204
    r = await party(client, party_id, friend)
    assert r.json()["leader_id"] == str(friend.user_id)
    assert r.json()["member_ids"] == [str(friend.user_id)]

    assert (await client.post(f"/parties/{party_id}/leave", headers=friend.headers)).status_code == 204
    assert (await party(client, party_id, friend)).status_code == 404
    assert [e.party_id for e in await events("party.disbanded")] == [party_id]
    # The player can create a new party.
    await create_party(client, friend)


async def test_kick(client):
    leader, a, b = new_player(), new_player(), new_player()
    party_id = await create_party(client, leader)
    for p in (a, b):
        await befriend(client, leader, p)
        await invite_and_join(client, party_id, leader, p)

    r = await client.post(f"/parties/{party_id}/kick", json={"user_id": str(b.user_id)}, headers=a.headers)
    assert (r.status_code, r.json()["error"]) == (403, "not_leader")
    r = await client.post(f"/parties/{party_id}/kick", json={"user_id": str(leader.user_id)}, headers=leader.headers)
    assert r.status_code == 400

    r = await client.post(f"/parties/{party_id}/kick", json={"user_id": str(b.user_id)}, headers=leader.headers)
    assert r.status_code == 204
    assert (await party(client, party_id, b)).status_code == 404
    # b is free to make their own party.
    await create_party(client, b)


def allocation_for(members: list[Player]) -> dict:
    return {
        "server_id": str(uuid.uuid4()),
        "address": "203.0.113.10",
        "port": 7000,
        "tickets": {str(m.user_id): f"ticket-{m.user_id}" for m in members},
        "expires_in": 30,
    }


async def test_launch(client, mocked):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)
    await invite_and_join(client, party_id, leader, friend)
    allocation = allocation_for([leader, friend])
    route = mocked.post("http://serverlist.test/allocations").mock(return_value=httpx.Response(201, json=allocation))

    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=friend.headers)
    assert (r.status_code, r.json()["error"]) == (403, "not_leader")

    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert r.status_code == 200
    assert r.json() == {
        "server_id": allocation["server_id"],
        "address": "203.0.113.10",
        "port": 7000,
        "ticket": f"ticket-{leader.user_id}",
    }
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer change-me-dev-social-service-secret"
    # Default settings when the leader sends none.
    assert json.loads(sent.content)["match_settings"] == {"bots": 2, "duration_seconds": 180}

    assert (await party(client, party_id, leader)).json()["status"] == "in_match"
    ready = await events("party.match_ready")
    assert ready[0].tickets[friend.user_id] == f"ticket-{friend.user_id}"
    assert [u.status for u in await events("party.updated")][-2:] == ["launching", "in_match"]

    # No new members or second launch during the match.
    other = new_player()
    await befriend(client, leader, other)
    r = await client.post(f"/parties/{party_id}/invite", json={"user_id": str(other.user_id)}, headers=leader.headers)
    assert r.json()["error"] == "not_in_lobby"
    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert r.json()["error"] == "not_in_lobby"


async def test_launch_passes_match_settings_to_the_server_list(client, mocked):
    leader = new_player()
    party_id = await create_party(client, leader)
    route = mocked.post("http://serverlist.test/allocations").mock(
        return_value=httpx.Response(201, json=allocation_for([leader]))
    )
    body = {"build_version": "0.1.0", "match_settings": {"bots": 8, "duration_seconds": 300}}
    r = await client.post(f"/parties/{party_id}/launch", json=body, headers=leader.headers)
    assert r.status_code == 422
    assert not route.called

    body["match_settings"]["bots"] = 0
    r = await client.post(f"/parties/{party_id}/launch", json=body, headers=leader.headers)
    assert r.status_code == 200
    assert json.loads(route.calls.last.request.content)["match_settings"] == {"bots": 0, "duration_seconds": 300}


async def test_launch_without_server_goes_back_to_lobby(client, mocked):
    leader = new_player()
    party_id = await create_party(client, leader)
    mocked.post("http://serverlist.test/allocations").mock(return_value=httpx.Response(503, json={}))

    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert (r.status_code, r.json()["error"]) == (503, "no_server_available")
    assert (await party(client, party_id, leader)).json()["status"] == "lobby"


async def test_launch_with_serverlist_down(client, mocked):
    leader = new_player()
    party_id = await create_party(client, leader)
    mocked.post("http://serverlist.test/allocations").mock(side_effect=httpx.ConnectError("refused"))

    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert (r.status_code, r.json()["error"]) == (503, "serverlist_unavailable")
    assert (await party(client, party_id, leader)).json()["status"] == "lobby"


async def test_every_change_publishes_a_snapshot_equal_to_the_party(client):
    leader, friend = new_player(), new_player()
    await befriend(client, leader, friend)
    party_id = await create_party(client, leader)
    await invite_and_join(client, party_id, leader, friend)
    await client.post(f"/parties/{party_id}/leave", headers=leader.headers)

    updates = await events("party.updated")
    assert [u.version for u in updates] == [1, 2, 3]  # created, joined, leader left
    stored = (await party(client, party_id, friend)).json()
    last = updates[-1]
    assert (str(last.leader_id), last.status, [str(m) for m in last.member_ids], last.version) == (
        stored["leader_id"],
        stored["status"],
        stored["member_ids"],
        stored["version"],
    )
    assert stored["leader_id"] == str(friend.user_id)


async def test_party_gone_during_allocation_gets_no_match_ready(client, monkeypatch):
    leader = new_player()
    party_id = await create_party(client, leader)

    async def allocate_while_leader_leaves(*_):
        await parties.remove_member(party_id, leader.user_id)
        return allocation_for([leader])

    monkeypatch.setattr(clients, "allocate_server", allocate_while_leader_leaves)
    r = await client.post(f"/parties/{party_id}/launch", json={"build_version": "0.1.0"}, headers=leader.headers)
    assert r.status_code == 409
    assert r.json()["error"] == "party_changed"
    assert await events("party.match_ready") == []
    assert [e.party_id for e in await events("party.disbanded")] == [party_id]
