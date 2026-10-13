import random
import uuid

from stats.redis_client import redis

from .conftest import play, result

HUGE = 10**6  # far above any other row in the shared dev database (and XP = 10 x kills fits an int)


async def ranked_players(kills: list[int]) -> list[uuid.UUID]:
    base = HUGE + random.randint(0, 10**3) * 1000
    players = [uuid.uuid4() for _ in kills]
    await play(*(result(p, kills=base + k, placement=i + 2) for i, (p, k) in enumerate(zip(players, kills))))
    return players


async def test_around_with_shared_ranks(client):
    first, tied_a, tied_b, last = await ranked_players([30, 20, 20, 10])

    r = await client.get(f"/leaderboards/kills/around/{tied_a}", params={"radius": 2})
    assert r.status_code == 200
    body = r.json()
    ranks = {e["user_id"]: e["rank"] for e in body["entries"]}
    top = ranks[str(first)]
    assert ranks[str(tied_a)] == ranks[str(tied_b)] == top + 1
    assert ranks[str(last)] == top + 3  # "1224" ranking
    assert body["player_rank"] == top + 1
    assert body["type"] == "kills"


async def test_top_page(client):
    players = await ranked_players([3, 2, 1])
    r = await client.get("/leaderboards/kills", params={"limit": 3})
    entries = r.json()["entries"]
    # Our players have the biggest totals of this run, unless another test made bigger ones.
    ours = [e for e in entries if e["user_id"] in {str(p) for p in players}]
    assert [e["user_id"] for e in ours] == [str(p) for p in players][: len(ours)]
    assert r.json()["total_players"] >= 3


async def test_xp_and_wins_boards(client):
    p = uuid.uuid4()
    await play(result(p, kills=HUGE, placement=1))
    xp = (await client.get(f"/leaderboards/xp/around/{p}", params={"radius": 0})).json()
    wins = (await client.get(f"/leaderboards/wins/around/{p}", params={"radius": 0})).json()
    assert xp["entries"][0]["value"] == 100 + 10 * HUGE + 200
    assert wins["entries"][0]["value"] == 1


async def test_rebuilt_from_postgres_if_redis_loses_it(client):
    first, second = await ranked_players([2, 1])
    await redis.flushdb()

    body = (await client.get(f"/leaderboards/kills/around/{second}", params={"radius": 1})).json()
    ids = [e["user_id"] for e in body["entries"]]
    assert ids.index(str(first)) < ids.index(str(second))


async def test_player_without_entry(client):
    body = (await client.get(f"/leaderboards/wins/around/{uuid.uuid4()}")).json()
    assert body["player_rank"] is None
    assert body["entries"] == []


async def test_validation(client):
    assert (await client.get("/leaderboards/deaths")).status_code == 422
    assert (await client.get("/leaderboards/kills", params={"limit": 101})).status_code == 422
    assert (await client.get(f"/leaderboards/kills/around/{uuid.uuid4()}", params={"radius": 26})).status_code == 422
