import uuid

from tpsarena_common.events import AchievementUnlocked, LevelUp, UserDeleted

from stats.worker import handle

from .conftest import play, progress_events, result


async def test_match_is_recorded_with_results_in_placement_order(client):
    a, b, c = (uuid.uuid4() for _ in range(3))
    event = await play(result(a, kills=2, placement=3), result(b, kills=9, deaths=1, placement=1), result(c, placement=2))

    r = await client.get(f"/matches/{event.match_id}")
    assert r.status_code == 200
    match = r.json()
    assert match["map"] == "arena"
    assert [p["user_id"] for p in match["players"]] == [str(b), str(c), str(a)]
    assert match["players"][0] == {"user_id": str(b), "kills": 9, "deaths": 1, "placement": 1, "xp_gained": 390}


async def test_unknown_match(client):
    r = await client.get(f"/matches/{uuid.uuid4()}")
    assert (r.status_code, r.json()["error"]) == (404, "match_not_found")


async def test_totals_add_up(client):
    p = uuid.uuid4()
    await play(result(p, kills=3, deaths=2, placement=1))
    await play(result(p, kills=1, deaths=2, placement=4))

    stats = (await client.get(f"/players/{p}/stats")).json()
    assert stats == {
        "user_id": str(p),
        "matches_played": 2,
        "wins": 1,
        "kills": 4,
        "deaths": 4,
        "kd_ratio": 1.0,
        "level": 1,
        "xp": (100 + 30 + 200) + (100 + 10),
    }


async def test_same_match_twice_counts_once(client):
    p = uuid.uuid4()
    match_id = uuid.uuid4()
    await play(result(p, kills=5), match_id=match_id)
    await play(result(p, kills=5), match_id=match_id)  # redelivered, or sent twice by the game server
    assert (await client.get(f"/players/{p}/stats")).json()["matches_played"] == 1


async def test_player_without_matches(client):
    stats = (await client.get(f"/players/{uuid.uuid4()}/stats")).json()
    assert (stats["matches_played"], stats["level"], stats["kd_ratio"]) == (0, 1, 0.0)


async def test_kd_ratio_without_deaths(client):
    p = uuid.uuid4()
    await play(result(p, kills=4, deaths=0))
    assert (await client.get(f"/players/{p}/stats")).json()["kd_ratio"] == 4.0


async def test_level_up_and_achievements(client):
    p = uuid.uuid4()
    event = await play(result(p, kills=12, deaths=0, placement=1))  # 100 + 120 + 200 = 420 XP

    progression = (await client.get(f"/progression/{p}")).json()
    assert (progression["level"], progression["xp_in_level"], progression["xp_to_next_level"]) == (1, 420, 1000)
    unlocked = {a["id"] for a in progression["achievements"] if a["unlocked_at"]}
    assert unlocked == {"first_match", "first_win", "match_10_kills", "flawless"}
    assert len(progression["achievements"]) == 7  # locked ones are listed too

    events = [e for e in await progress_events() if e.user_id == p]
    assert {e.achievement_id for e in events if isinstance(e, AchievementUnlocked)} == unlocked
    assert all(e.match_id == event.match_id for e in events)

    await play(result(p, kills=90, deaths=1, placement=1))  # 100 + 900 + 200 = 1200 -> 1620 XP: level 2
    progression = (await client.get(f"/progression/{p}")).json()
    assert (progression["level"], progression["xp_in_level"]) == (2, 620)
    assert [e.level for e in await progress_events() if isinstance(e, LevelUp) and e.user_id == p] == [2]
    # 102 kills: kills_100 unlocks now; the others don't unlock a second time.
    unlocked_events = [e.achievement_id for e in await progress_events() if isinstance(e, AchievementUnlocked) and e.user_id == p]
    assert sorted(unlocked_events) == sorted([*unlocked, "kills_100"])


async def test_deleted_player(client):
    gone, stays = uuid.uuid4(), uuid.uuid4()
    event = await play(result(gone, kills=3, placement=1), result(stays, kills=1, placement=2))

    await handle(UserDeleted(user_id=gone))

    match = (await client.get(f"/matches/{event.match_id}")).json()
    assert [p["user_id"] for p in match["players"]] == [None, str(stays)]
    assert match["players"][0]["kills"] == 3
    assert (await client.get(f"/players/{gone}/stats")).json()["matches_played"] == 0
    progression = (await client.get(f"/progression/{gone}")).json()
    assert all(a["unlocked_at"] is None for a in progression["achievements"])
    r = await client.get(f"/leaderboards/kills/around/{gone}")
    assert r.json()["player_rank"] is None


async def test_routes_need_a_token(client):
    r = await client.get(f"/players/{uuid.uuid4()}/stats", headers={"authorization": ""})
    assert r.status_code == 401


async def test_progress_events_wait_in_the_outbox_while_redis_is_down(monkeypatch):
    from redis.asyncio import Redis

    from stats.events import outbox

    p = uuid.uuid4()
    down = Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True)
    monkeypatch.setattr(outbox, "redis", down)
    await play(result(p, kills=1, placement=1))  # recorded; the notifications wait
    assert await progress_events() == []

    monkeypatch.undo()
    await outbox.flush()  # what the worker does every few seconds
    assert {e.TYPE for e in await progress_events() if e.user_id == p} >= {"progress.achievement_unlocked"}
    await down.aclose()
