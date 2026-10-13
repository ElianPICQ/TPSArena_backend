import httpx

from .conftest import DELETED_USERS, Player, events, new_player


async def befriend(client: httpx.AsyncClient, a: Player, b: Player) -> None:
    request_id = (await client.post("/friends/requests", json={"user_id": str(b.user_id)}, headers=a.headers)).json()[
        "request_id"
    ]
    r = await client.post(f"/friends/requests/{request_id}/accept", headers=b.headers)
    assert r.status_code == 200


async def friends_of(client: httpx.AsyncClient, p: Player) -> dict:
    return (await client.get("/friends", headers=p.headers)).json()


async def test_request_and_accept(client):
    alice, bob = new_player(), new_player()
    r = await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    request_id = r.json()["request_id"]

    assert [p["user_id"] for p in (await friends_of(client, alice))["outgoing"]] == [str(bob.user_id)]
    assert [p["request_id"] for p in (await friends_of(client, bob))["incoming"]] == [request_id]

    r = await client.post(f"/friends/requests/{request_id}/accept", headers=bob.headers)
    assert r.status_code == 200
    assert r.json()["user_id"] == str(alice.user_id)

    for me, other in ((alice, bob), (bob, alice)):
        listing = await friends_of(client, me)
        assert [f["user_id"] for f in listing["friends"]] == [str(other.user_id)]
        assert listing["incoming"] == listing["outgoing"] == []

    assert [e.TYPE for e in await events()] == ["friend.request_sent", "friend.added"]


async def test_crossed_requests_become_friends(client):
    alice, bob = new_player(), new_player()
    await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)
    r = await client.post("/friends/requests", json={"user_id": str(alice.user_id)}, headers=bob.headers)
    assert r.status_code == 201
    assert r.json() == {"status": "accepted", "request_id": None}
    assert len((await friends_of(client, alice))["friends"]) == 1


async def test_request_errors(client):
    alice, bob = new_player(), new_player()
    to = lambda p: {"user_id": str(p.user_id)}  # noqa: E731

    r = await client.post("/friends/requests", json=to(alice), headers=alice.headers)
    assert (r.status_code, r.json()["error"]) == (400, "cannot_friend_self")

    ghost = new_player()
    DELETED_USERS.add(str(ghost.user_id))
    r = await client.post("/friends/requests", json=to(ghost), headers=alice.headers)
    assert (r.status_code, r.json()["error"]) == (404, "user_not_found")

    await client.post("/friends/requests", json=to(bob), headers=alice.headers)
    r = await client.post("/friends/requests", json=to(bob), headers=alice.headers)
    assert (r.status_code, r.json()["error"]) == (409, "request_exists")

    request_id = (await friends_of(client, bob))["incoming"][0]["request_id"]
    await client.post(f"/friends/requests/{request_id}/accept", headers=bob.headers)
    r = await client.post("/friends/requests", json=to(bob), headers=alice.headers)
    assert (r.status_code, r.json()["error"]) == (409, "already_friends")


async def test_only_recipient_can_accept(client):
    alice, bob = new_player(), new_player()
    request_id = (await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)).json()[
        "request_id"
    ]
    r = await client.post(f"/friends/requests/{request_id}/accept", headers=alice.headers)
    assert r.status_code == 404


async def test_decline_and_cancel(client):
    alice, bob, carol = new_player(), new_player(), new_player()
    declined = (await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)).json()[
        "request_id"
    ]
    cancelled = (await client.post("/friends/requests", json={"user_id": str(carol.user_id)}, headers=alice.headers)).json()[
        "request_id"
    ]

    assert (await client.post(f"/friends/requests/{declined}/decline", headers=bob.headers)).status_code == 204
    assert (await client.post(f"/friends/requests/{cancelled}/decline", headers=alice.headers)).status_code == 204
    assert (await friends_of(client, alice))["outgoing"] == []
    assert len(await events("friend.request_removed")) == 2

    # A stranger can't touch someone else's request.
    again = (await client.post("/friends/requests", json={"user_id": str(bob.user_id)}, headers=alice.headers)).json()[
        "request_id"
    ]
    assert (await client.post(f"/friends/requests/{again}/decline", headers=carol.headers)).status_code == 404


async def test_remove_friend(client):
    alice, bob = new_player(), new_player()
    await befriend(client, alice, bob)
    assert (await client.delete(f"/friends/{alice.user_id}", headers=bob.headers)).status_code == 204
    assert (await friends_of(client, alice))["friends"] == []
    assert (await client.delete(f"/friends/{alice.user_id}", headers=bob.headers)).status_code == 404
    assert len(await events("friend.removed")) == 1
