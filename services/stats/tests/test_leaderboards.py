import uuid

from stats.redis_client import redis

from .conftest import play, result


async def players_with_kills(kills: list[int]) -> list[uuid.UUID]:
    players = [uuid.uuid4() for _ in kills]
    await play(*(result(p, kills=k, placement=i + 2) for i, (p, k) in enumerate(zip(players, kills))))
    return players


async def test_top_page_with_shared_ranks(client):
    first, tied_a, tied_b, last = await players_with_kills([30, 20, 20, 10])

    body = (await client.get("/leaderboards/kills")).json()
    assert body["type"] == "kills"
    assert body["total_players"] == 4
    ranks = {e["user_id"]: e["rank"] for e in body["entries"]}
    assert ranks == {str(first): 1, str(tied_a): 2, str(tied_b): 2, str(last): 4}  # "1224" ranking
    assert [e["value"] for e in body["entries"]] == [30, 20, 20, 10]


async def test_paging(client):
    players = await players_with_kills([5, 4, 3, 2, 1])
    body = (await client.get("/leaderboards/kills", params={"offset": 2, "limit": 2})).json()
    assert [e["user_id"] for e in body["entries"]] == [str(players[2]), str(players[3])]
    assert [e["rank"] for e in body["entries"]] == [3, 4]


async def test_around(client):
    players = await players_with_kills([50, 40, 30, 20, 10])
    body = (await client.get(f"/leaderboards/kills/around/{players[2]}", params={"radius": 1})).json()
    assert body["player_rank"] == 3
    assert [e["user_id"] for e in body["entries"]] == [str(p) for p in players[1:4]]

    # At the top, the window is cut, not shifted.
    body = (await client.get(f"/leaderboards/kills/around/{players[0]}", params={"radius": 2})).json()
    assert [e["rank"] for e in body["entries"]] == [1, 2, 3]


async def test_xp_and_wins_boards(client):
    p = uuid.uuid4()
    await play(result(p, kills=3, placement=1))
    xp = (await client.get("/leaderboards/xp")).json()
    wins = (await client.get("/leaderboards/wins")).json()
    assert xp["entries"] == [{"rank": 1, "user_id": str(p), "value": 100 + 30 + 200}]
    assert wins["entries"] == [{"rank": 1, "user_id": str(p), "value": 1}]


async def test_rebuilt_from_postgres_if_redis_loses_it(client):
    first, second = await players_with_kills([2, 1])
    await redis.flushdb()

    body = (await client.get("/leaderboards/kills")).json()
    assert [e["user_id"] for e in body["entries"]] == [str(first), str(second)]


async def test_player_without_entry(client):
    body = (await client.get(f"/leaderboards/wins/around/{uuid.uuid4()}")).json()
    assert body["player_rank"] is None
    assert body["entries"] == []


async def test_validation(client):
    assert (await client.get("/leaderboards/deaths")).status_code == 422
    assert (await client.get("/leaderboards/kills", params={"limit": 101})).status_code == 422
    assert (await client.get(f"/leaderboards/kills/around/{uuid.uuid4()}", params={"radius": 26})).status_code == 422
