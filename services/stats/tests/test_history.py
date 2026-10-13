import base64
import uuid
from datetime import UTC, datetime, timedelta

from tpsarena_common.events import UserDeleted

from stats.worker import handle

from .conftest import play, result


async def history(client, user_id: uuid.UUID, **params) -> dict:
    r = await client.get(f"/players/{user_id}/matches", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_newest_first_with_own_results(client):
    me, other = uuid.uuid4(), uuid.uuid4()
    start = datetime.now(UTC) - timedelta(days=1)
    old = await play(result(me, kills=1, placement=2), result(other, placement=1), ended_at=start)
    new = await play(result(me, kills=7, deaths=2, placement=1), ended_at=start + timedelta(hours=1))

    body = await history(client, me)
    assert [m["match_id"] for m in body["matches"]] == [str(new.match_id), str(old.match_id)]
    assert body["matches"][0] == {
        "match_id": str(new.match_id),
        "map": "arena",
        "started_at": body["matches"][0]["started_at"],
        "ended_at": body["matches"][0]["ended_at"],
        "kills": 7,
        "deaths": 2,
        "placement": 1,
        "player_count": 1,
        "xp_gained": 100 + 70 + 200,
    }
    assert body["matches"][1]["player_count"] == 2
    assert body["next_cursor"] is None


async def test_pages_with_cursor(client):
    me = uuid.uuid4()
    start = datetime.now(UTC) - timedelta(days=2)
    ids = [(await play(result(me), ended_at=start + timedelta(minutes=i))).match_id for i in range(5)]
    newest_first = [str(i) for i in reversed(ids)]

    first = await history(client, me, limit=2)
    assert [m["match_id"] for m in first["matches"]] == newest_first[:2]

    # A match finishing while the player scrolls doesn't shift the next pages.
    await play(result(me))

    second = await history(client, me, limit=2, before=first["next_cursor"])
    assert [m["match_id"] for m in second["matches"]] == newest_first[2:4]
    third = await history(client, me, limit=2, before=second["next_cursor"])
    assert [m["match_id"] for m in third["matches"]] == newest_first[4:]
    assert third["next_cursor"] is None


async def test_matches_ending_at_the_same_time(client):
    me = uuid.uuid4()
    same = datetime.now(UTC) - timedelta(days=3)
    ids = {str((await play(result(me), ended_at=same)).match_id) for _ in range(3)}

    seen = []
    cursor = None
    while True:
        body = await history(client, me, limit=1, **({"before": cursor} if cursor else {}))
        seen += [m["match_id"] for m in body["matches"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids)  # each exactly once


async def test_player_without_matches(client):
    assert await history(client, uuid.uuid4()) == {"matches": [], "next_cursor": None}


async def test_deleted_player_has_no_history(client):
    me, other = uuid.uuid4(), uuid.uuid4()
    await play(result(me), result(other, placement=1))
    await handle(UserDeleted(user_id=me))
    assert (await history(client, me))["matches"] == []
    assert (await history(client, other))["matches"][0]["player_count"] == 2


async def test_validation(client):
    me = uuid.uuid4()
    r = await client.get(f"/players/{me}/matches", params={"before": "not-a-cursor"})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_cursor")
    naive = base64.urlsafe_b64encode(f"2026-01-01T00:00:00|{uuid.uuid4()}".encode()).decode()
    assert (await client.get(f"/players/{me}/matches", params={"before": naive})).status_code == 400
    assert (await client.get(f"/players/{me}/matches", params={"limit": 51})).status_code == 422
    assert (await client.get(f"/players/{me}/matches", params={"limit": 0})).status_code == 422
